#!/usr/bin/env python3
"""
Audio Backend - Idempotent PulseAudio/PipeWire control for Audio Sync Master.
Manages virtual sinks, loopbacks, and delay without breaking existing setups.
Tracks own module IDs to avoid destroying other apps' modules on cleanup.
"""

import re
import time
import threading
from typing import Optional, Tuple
from config import config
from pulse_utils import (
    run_cmd, find_module_id, find_loopback_info,
    find_bt_sink_by_description, list_movable_sink_inputs,
    validate_sink_name, auto_detect_analog_sink, auto_detect_bt_sink,
    get_bt_codec_info, suggest_jack_delay, is_pipewire,
)


class AudioState:
    """Current state of the audio system."""

    def __init__(self):
        self.virtual_sink_exists: bool = False
        self.virtual_sink_module_id: Optional[int] = None
        self.jack_loopback_exists: bool = False
        self.jack_loopback_module_id: Optional[int] = None
        self.jack_loopback_delay: Optional[int] = None
        self.bt_loopback_exists: bool = False
        self.bt_loopback_module_id: Optional[int] = None
        self.bt_sink_name: Optional[str] = None
        self.bt_connected: bool = False
        self.bt_codec: Optional[str] = None
        self.bt_latency_ms: Optional[int] = None
        self.jack_sink_available: bool = False
        self.default_sink: Optional[str] = None
        self.using_pipewire: bool = False
        self.suggested_delay: Optional[int] = None


class AudioBackend:
    """
    Idempotent audio backend for PulseAudio/PipeWire.
    Only makes changes when necessary.
    Tracks own module IDs for safe cleanup.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reload_config()
        self._state = AudioState()
        # Track module IDs we created (for safe cleanup)
        self._own_module_ids: set[int] = set()

    def _reload_config(self):
        """Reload sink names from config, auto-detecting if empty."""
        self.virtual_sink = config.get("virtual_sink", "audio_master")
        self.jack_sink = config.get("jack_sink", "")
        self.bt_speaker_name = config.get("bt_speaker_name", "")

        # Auto-detect sinks if not configured
        if not self.jack_sink:
            detected = auto_detect_analog_sink()
            if detected:
                self.jack_sink = detected
                config.set("jack_sink", detected)

    def get_state(self) -> AudioState:
        """Refresh and return current audio state (thread-safe)."""
        with self._lock:
            self._refresh_state()
            return self._state

    def _refresh_state(self):
        """Query PulseAudio/PipeWire for current state."""
        state = AudioState()
        state.using_pipewire = is_pipewire()

        # Check sinks
        sinks_output = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks_output:
            state.virtual_sink_exists = self.virtual_sink in sinks_output
            state.jack_sink_available = bool(self.jack_sink) and self.jack_sink in sinks_output

            # Find BT sink
            if self.bt_speaker_name:
                state.bt_sink_name = find_bt_sink_by_description(self.bt_speaker_name)
            else:
                state.bt_sink_name = auto_detect_bt_sink()
            state.bt_connected = state.bt_sink_name is not None

        # BT codec info
        if state.bt_connected:
            codec_info = get_bt_codec_info()
            if codec_info:
                state.bt_codec = codec_info.get('codec', 'SBC')
                state.bt_latency_ms = codec_info.get('latency_ms', 0)
                state.suggested_delay = suggest_jack_delay(state.bt_codec)

        # Check default sink
        info_output = run_cmd('pactl', 'info', capture=True)
        if info_output:
            match = re.search(r'Default Sink: (\S+)', info_output)
            if match:
                state.default_sink = match.group(1)

        # Check modules for loopbacks and virtual sink
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if modules_output:
            state.virtual_sink_module_id = find_module_id(
                modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}'
            )
            state.virtual_sink_exists = state.virtual_sink_module_id is not None

            # Find jack loopback
            if self.jack_sink:
                jack_loop = find_loopback_info(
                    modules_output, self.jack_sink, f'{self.virtual_sink}.monitor'
                )
                if jack_loop:
                    state.jack_loopback_exists = True
                    state.jack_loopback_module_id = jack_loop[0]
                    state.jack_loopback_delay = jack_loop[1]

            # Find BT loopback
            if state.bt_sink_name:
                bt_loop = find_loopback_info(
                    modules_output, state.bt_sink_name, f'{self.virtual_sink}.monitor'
                )
                if bt_loop:
                    state.bt_loopback_exists = True
                    state.bt_loopback_module_id = bt_loop[0]

        self._state = state

    def is_setup_complete(self) -> bool:
        with self._lock:
            self._refresh_state()
            return (
                self._state.virtual_sink_exists and
                self._state.jack_loopback_exists and
                self._state.default_sink == self.virtual_sink
            )

    def setup_sync(self, jack_delay_ms: Optional[int] = None) -> Tuple[bool, str]:
        """
        Idempotent setup of audio sync.
        Only creates/modifies what's missing or wrong.
        Returns (success, message).
        """
        with self._lock:
            self._reload_config()

            if not self.jack_sink:
                return (False, "No analog output sink found. Configure in Settings.")

            if not validate_sink_name(self.jack_sink):
                return (False, f"Invalid jack sink name: {self.jack_sink}")

            if jack_delay_ms is None:
                jack_delay_ms = config.jack_delay

            self._refresh_state()
            messages = []

            # 1. Create virtual sink if missing
            if not self._state.virtual_sink_exists:
                output = run_cmd(
                    'pactl', 'load-module', 'module-null-sink',
                    f'sink_name={self.virtual_sink}',
                    'sink_properties=device.description="Audio_Master"',
                    capture=True
                )
                if output and output.strip():
                    try:
                        mid = int(output.strip())
                        self._own_module_ids.add(mid)
                    except ValueError:
                        pass
                    messages.append("Created virtual master sink")
                    time.sleep(0.3)
                else:
                    return (False, "Failed to create virtual sink")

            # 2. Set as default sink
            self._refresh_state()
            if self._state.default_sink != self.virtual_sink:
                run_cmd('pactl', 'set-default-sink', self.virtual_sink)
                messages.append("Set audio_master as default")

            # 3. Create/update jack loopback
            if not self._state.jack_loopback_exists:
                output = run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={self.jack_sink}',
                    f'latency_msec={jack_delay_ms}',
                    capture=True
                )
                if output and output.strip():
                    try:
                        mid = int(output.strip())
                        self._own_module_ids.add(mid)
                    except ValueError:
                        pass
                messages.append(f"Created Jack loopback ({jack_delay_ms}ms)")
            elif self._state.jack_loopback_delay != jack_delay_ms:
                self._update_jack_delay(jack_delay_ms)
                messages.append(f"Updated Jack delay to {jack_delay_ms}ms")

            # 4. Attach Bluetooth if connected
            self._refresh_state()
            if self._state.bt_connected and not self._state.bt_loopback_exists:
                run_cmd('pactl', 'set-sink-mute', self._state.bt_sink_name, '0')
                run_cmd('pactl', 'set-sink-volume', self._state.bt_sink_name, '100%')
                output = run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={self._state.bt_sink_name}',
                    'latency_msec=1',
                    capture=True
                )
                if output and output.strip():
                    try:
                        mid = int(output.strip())
                        self._own_module_ids.add(mid)
                    except ValueError:
                        pass
                messages.append("Attached Bluetooth speaker")

            if not messages:
                return (True, "Audio sync already configured correctly")

            # 5. Restore EQ routing if EQ was enabled
            self._restore_eq_routing()

            return (True, "; ".join(messages))

    def _restore_eq_routing(self):
        """If EQ is enabled, make sure audio routes through it."""
        sinks = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks and 'eq_sink' in sinks:
            run_cmd('pactl', 'set-default-sink', 'eq_sink')
            for input_id in list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, 'eq_sink')

    def _update_jack_delay(self, delay_ms: int):
        """Update jack loopback delay by recreating it."""
        self._refresh_state()

        if self._state.jack_loopback_module_id:
            self._own_module_ids.discard(self._state.jack_loopback_module_id)
            run_cmd('pactl', 'unload-module', str(self._state.jack_loopback_module_id))
            time.sleep(0.1)

        output = run_cmd(
            'pactl', 'load-module', 'module-loopback',
            f'source={self.virtual_sink}.monitor',
            f'sink={self.jack_sink}',
            f'latency_msec={delay_ms}',
            capture=True
        )
        if output and output.strip():
            try:
                mid = int(output.strip())
                self._own_module_ids.add(mid)
            except ValueError:
                pass

    def set_jack_delay(self, delay_ms: int) -> bool:
        """Set Jack delay without full reset."""
        delay_ms = max(0, min(300, delay_ms))
        config.jack_delay = delay_ms

        with self._lock:
            self._refresh_state()
            if self._state.jack_loopback_exists:
                self._update_jack_delay(delay_ms)
                return True
        return False

    def get_jack_delay(self) -> int:
        with self._lock:
            self._refresh_state()
            if self._state.jack_loopback_delay is not None:
                return self._state.jack_loopback_delay
        return config.jack_delay

    def cleanup(self):
        """Remove only modules created by this app."""
        with self._lock:
            # First try targeted cleanup using tracked IDs
            for mid in list(self._own_module_ids):
                run_cmd('pactl', 'unload-module', str(mid))
            self._own_module_ids.clear()

            # Also unload by matching our specific sink names
            modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
            if modules_output:
                # Unload our virtual sink
                mid = find_module_id(modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}')
                if mid:
                    run_cmd('pactl', 'unload-module', str(mid))

                # Unload our loopbacks (ones using our monitor)
                from pulse_utils import parse_module_blocks
                for block in parse_module_blocks(modules_output):
                    if block['name'] == 'module-loopback' and f'{self.virtual_sink}.monitor' in block['argument']:
                        if block['id'] is not None:
                            run_cmd('pactl', 'unload-module', str(block['id']))

            time.sleep(0.3)

    def move_streams_to_master(self):
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.virtual_sink)


# Global backend instance
audio = AudioBackend()
