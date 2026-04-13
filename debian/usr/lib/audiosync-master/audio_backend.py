#!/usr/bin/env python3
"""
Audio Backend - Idempotent PulseAudio/PipeWire control for Audio Sync Master.
Manages virtual sinks, loopbacks, and delay without breaking existing setups.
Tracks own module IDs to avoid destroying other apps' modules on cleanup.

On every setup_sync():
  1. Detect hardware (card, analog sink, BT sink)
  2. Switch card profile to analog if needed
  3. Create virtual master sink
  4. Create jack loopback with delay
  5. Create BT loopback with safe buffer
  6. Set default to virtual master
"""

import re
import subprocess
import time
import threading
from typing import Optional, Tuple
from config import config
from pulse_utils import (
    run_cmd, find_module_id, find_loopback_info, parse_module_blocks,
    find_bt_sink_by_description, list_movable_sink_inputs,
    validate_sink_name, auto_detect_bt_sink,
    get_bt_codec_info, suggest_jack_delay, is_pipewire, list_sinks,
)


# PipeWire needs longer settle times than PulseAudio
_PW_SETTLE = 0.5
_PA_SETTLE = 0.2


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
    Detects hardware, switches profiles, creates routing — fully automatic.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._state = AudioState()
        self._own_module_ids: set[int] = set()
        self._pipewire = is_pipewire()
        self._settle = _PW_SETTLE if self._pipewire else _PA_SETTLE
        self.virtual_sink = config.get("virtual_sink", "audio_master")
        self.bt_speaker_name = config.get("bt_speaker_name", "")
        self._volume_monitor: Optional[subprocess.Popen] = None
        self._last_volume: Optional[str] = None
        self._last_mute: Optional[str] = None

    def _detect_card_and_sink(self) -> Tuple[str, str, str]:
        """
        Detect the sound card, find the analog sink name, and the headphone port.
        Returns (card_name, analog_sink_name, port_name).
        This always queries live hardware — never relies on stale config.
        """
        card_name = ""
        analog_sink = ""
        port_name = ""

        # Find the ALSA card
        cards_output = run_cmd('pactl', 'list', 'cards', capture=True)
        if not cards_output:
            return card_name, analog_sink, port_name

        for card_block in cards_output.split('Card #')[1:]:
            # Skip bluetooth cards
            if 'bluez' in card_block.lower():
                continue

            # Get card name
            name_match = re.search(r'Name:\s+(\S+)', card_block)
            if not name_match:
                continue
            card_name = name_match.group(1)

            # Check if analog profile exists and is available
            has_analog = False
            for line in card_block.splitlines():
                if 'output:analog-stereo' in line and 'available: yes' in line:
                    has_analog = True
                    break

            if not has_analog:
                continue

            # Build the expected analog sink name
            # alsa_card.pci-XXX -> alsa_output.pci-XXX.analog-stereo
            if card_name.startswith('alsa_card.'):
                hw_id = card_name[len('alsa_card.'):]
                analog_sink = f'alsa_output.{hw_id}.analog-stereo'

            # Find headphones port
            for line in card_block.splitlines():
                if 'analog-output-headphones' in line and 'available' in line and 'not available' not in line:
                    port_name = 'analog-output-headphones'
                    break
            if not port_name:
                # Fall back to speakers
                for line in card_block.splitlines():
                    if 'analog-output-speaker' in line and 'available' in line and 'not available' not in line:
                        port_name = 'analog-output-speaker'
                        break

            if analog_sink:
                break

        return card_name, analog_sink, port_name

    def _ensure_analog_sink_available(self, card_name: str, analog_sink: str, port_name: str) -> bool:
        """
        Switch sound card profile to analog and set port.
        Returns True if the analog sink becomes available.
        """
        if not card_name or not analog_sink:
            return False

        # Check if sink already exists
        sinks_short = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks_short and analog_sink in sinks_short:
            # Already available, just make sure port is set
            if port_name:
                run_cmd('pactl', 'set-sink-port', analog_sink, port_name)
            return True

        # Switch profile to analog stereo duplex
        run_cmd('pactl', 'set-card-profile', card_name,
                'output:analog-stereo+input:analog-stereo')
        time.sleep(self._settle)

        # Set the port
        if port_name:
            run_cmd('pactl', 'set-sink-port', analog_sink, port_name)

        # Verify it appeared
        sinks_short = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        return bool(sinks_short and analog_sink in sinks_short)

    def get_state(self) -> AudioState:
        """Refresh and return current audio state (thread-safe)."""
        with self._lock:
            self._refresh_state()
            return self._state

    def _refresh_state(self):
        """Query PulseAudio/PipeWire for current state."""
        state = AudioState()
        state.using_pipewire = self._pipewire

        sinks_output = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks_output:
            state.virtual_sink_exists = self.virtual_sink in sinks_output
            jack_sink = config.get("jack_sink", "")
            state.jack_sink_available = bool(jack_sink) and jack_sink in sinks_output

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

        # Default sink
        info_output = run_cmd('pactl', 'info', capture=True)
        if info_output:
            match = re.search(r'Default Sink: (\S+)', info_output)
            if match:
                state.default_sink = match.group(1)

        # Module state
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if modules_output:
            state.virtual_sink_module_id = find_module_id(
                modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}'
            )
            state.virtual_sink_exists = state.virtual_sink_module_id is not None

            jack_sink = config.get("jack_sink", "")
            if jack_sink:
                jack_loop = find_loopback_info(
                    modules_output, jack_sink, f'{self.virtual_sink}.monitor'
                )
                if jack_loop:
                    state.jack_loopback_exists = True
                    state.jack_loopback_module_id = jack_loop[0]
                    state.jack_loopback_delay = jack_loop[1]

            if state.bt_sink_name:
                bt_loop = find_loopback_info(
                    modules_output, state.bt_sink_name, f'{self.virtual_sink}.monitor'
                )
                if bt_loop:
                    state.bt_loopback_exists = True
                    state.bt_loopback_module_id = bt_loop[0]

        self._state = state

    def _cleanup_stale_modules(self, correct_jack_sink: str, correct_bt_sink: str, jack_delay: int):
        """
        Remove loopbacks/null-sinks from previous runs that are WRONG.
        A module is stale if:
          - Loopback points to wrong sink (e.g. hdmi instead of analog)
          - BT loopback has wrong buffer size (<5ms on PipeWire)
          - Duplicate null sinks exist
        Correct modules are LEFT ALONE for true idempotency.
        """
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if not modules_output:
            return

        cleaned = False
        null_sink_count = 0
        bt_buffer_min = 5 if self._pipewire else 1

        for block in parse_module_blocks(modules_output):
            args = block['argument']

            if block['name'] == 'module-null-sink' and f'sink_name={self.virtual_sink}' in args:
                null_sink_count += 1
                # Remove duplicate null sinks (keep only the first)
                if null_sink_count > 1 and block['id'] is not None:
                    run_cmd('pactl', 'unload-module', str(block['id']))
                    cleaned = True

            elif block['name'] == 'module-loopback' and f'{self.virtual_sink}.monitor' in args:
                is_jack = f'sink={correct_jack_sink}' in args
                is_bt = correct_bt_sink and f'sink={correct_bt_sink}' in args

                if is_jack or is_bt:
                    # Check if BT loopback has bad buffer
                    if is_bt:
                        lat_match = re.search(r'latency_msec=(\d+)', args)
                        if lat_match and int(lat_match.group(1)) < bt_buffer_min:
                            # Bad BT buffer — remove stale
                            if block['id'] is not None:
                                run_cmd('pactl', 'unload-module', str(block['id']))
                                cleaned = True
                    # Jack and BT with correct sink are fine — leave them
                else:
                    # Wrong sink (e.g. hdmi-stereo) — stale, remove
                    if block['id'] is not None:
                        run_cmd('pactl', 'unload-module', str(block['id']))
                        cleaned = True

        if cleaned:
            time.sleep(self._settle)

    def setup_sync(self, jack_delay_ms: Optional[int] = None) -> Tuple[bool, str]:
        """
        Full idempotent setup:
        0. Clean up stale modules from previous runs
        1. Detect hardware (card, analog sink, port)
        2. Switch card profile to analog
        3. Create virtual master sink
        4. Create jack loopback with delay
        5. Create BT loopback with safe buffer
        6. Set default to virtual master
        """
        with self._lock:
            if jack_delay_ms is None:
                jack_delay_ms = config.jack_delay
            if jack_delay_ms <= 0:
                jack_delay_ms = 115
                config.jack_delay = jack_delay_ms

            messages = []

            # === STEP 1: Detect hardware ===
            card_name, analog_sink, port_name = self._detect_card_and_sink()

            if not analog_sink:
                # Fall back to any non-BT sink
                for s in list_sinks():
                    if not s['is_bluetooth'] and s['name'] != self.virtual_sink and s['name'].startswith('alsa_'):
                        analog_sink = s['name']
                        break

            if not analog_sink:
                return (False, "No audio output found. Check your sound card.")

            # Save detected values to config
            if config.get("jack_sink") != analog_sink:
                config.set("jack_sink", analog_sink, save=False)
            if card_name and config.get("jack_card") != card_name:
                config.set("jack_card", card_name, save=False)
            if port_name and config.get("jack_port") != port_name:
                config.set("jack_port", port_name, save=False)
            config.save()

            jack_sink = analog_sink

            # === STEP 1.5: Clean up stale modules from previous runs ===
            # Find BT sink for stale check
            bt_sink = ""
            if self.bt_speaker_name:
                bt_sink = find_bt_sink_by_description(self.bt_speaker_name) or ""
            else:
                bt_sink = auto_detect_bt_sink() or ""
            self._cleanup_stale_modules(jack_sink, bt_sink, jack_delay_ms)

            # === STEP 2: Switch card profile to analog ===
            if card_name:
                available = self._ensure_analog_sink_available(card_name, jack_sink, port_name)
                if available:
                    messages.append("Analog output ready")
                else:
                    return (False, f"Failed to activate analog output: {jack_sink}")

            # === STEP 3: Create virtual master sink ===
            self._refresh_state()

            if not self._state.virtual_sink_exists:
                output = run_cmd(
                    'pactl', 'load-module', 'module-null-sink',
                    f'sink_name={self.virtual_sink}',
                    'sink_properties=device.description="Audio_Master"',
                    capture=True
                )
                if output and output.strip():
                    self._track_module(output.strip())
                    messages.append("Created virtual master sink")
                    time.sleep(self._settle)
                else:
                    return (False, "Failed to create virtual sink")

            # === STEP 4: Set as default sink ===
            self._refresh_state()
            if self._state.default_sink != self.virtual_sink:
                run_cmd('pactl', 'set-default-sink', self.virtual_sink)
                messages.append("Set audio_master as default")

            # === STEP 5: Create/update jack loopback ===
            self._refresh_state()
            if not self._state.jack_loopback_exists:
                output = run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={jack_sink}',
                    f'latency_msec={jack_delay_ms}',
                    capture=True
                )
                if output and output.strip():
                    self._track_module(output.strip())
                messages.append(f"Created Jack loopback ({jack_delay_ms}ms)")
                time.sleep(self._settle)
            elif self._state.jack_loopback_delay != jack_delay_ms:
                self._update_jack_delay(jack_delay_ms, jack_sink)
                messages.append(f"Updated Jack delay to {jack_delay_ms}ms")

            # === STEP 6: Attach Bluetooth ===
            self._refresh_state()
            bt_name = self.bt_speaker_name
            if self._state.bt_connected and not self._state.bt_loopback_exists:
                run_cmd('pactl', 'set-sink-mute', self._state.bt_sink_name, '0')
                run_cmd('pactl', 'set-sink-volume', self._state.bt_sink_name, '100%')
                bt_buffer = 10 if self._pipewire else 5
                output = run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={self._state.bt_sink_name}',
                    f'latency_msec={bt_buffer}',
                    capture=True
                )
                if output and output.strip():
                    self._track_module(output.strip())
                messages.append("Attached Bluetooth speaker")

            # === STEP 7: Move existing streams to master ===
            for input_id in list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, self.virtual_sink)

            if not messages:
                self.start_volume_sync()
                return (True, "Audio sync already configured correctly")

            # Restore EQ routing if enabled
            self._restore_eq_routing()

            # Start volume sync so FN keys work
            self.start_volume_sync()

            return (True, "; ".join(messages))

    def _track_module(self, output: str):
        """Track a module ID we created."""
        try:
            mid = int(output.strip())
            self._own_module_ids.add(mid)
        except ValueError:
            pass

    def _restore_eq_routing(self):
        sinks = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks and 'eq_sink' in sinks:
            run_cmd('pactl', 'set-default-sink', 'eq_sink')
            for input_id in list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, 'eq_sink')

    def _update_jack_delay(self, delay_ms: int, jack_sink: str = ""):
        """Update jack loopback delay by recreating it."""
        if not jack_sink:
            jack_sink = config.get("jack_sink", "")
        if not jack_sink:
            return

        self._refresh_state()

        # Unload old
        if self._state.jack_loopback_module_id:
            self._own_module_ids.discard(self._state.jack_loopback_module_id)
            run_cmd('pactl', 'unload-module', str(self._state.jack_loopback_module_id))
            time.sleep(self._settle)

        # Create new
        output = run_cmd(
            'pactl', 'load-module', 'module-loopback',
            f'source={self.virtual_sink}.monitor',
            f'sink={jack_sink}',
            f'latency_msec={delay_ms}',
            capture=True
        )
        if output and output.strip():
            self._track_module(output.strip())
        time.sleep(self._settle)

    def set_jack_delay(self, delay_ms: int) -> bool:
        """Set Jack delay without full reset."""
        delay_ms = max(1, min(300, delay_ms))
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

    def start_volume_sync(self):
        """
        Start monitoring volume changes on audio_master and mirror them
        to all real output sinks. This makes FN volume keys work.
        Runs `pactl subscribe` in a background thread.
        """
        if self._volume_monitor is not None:
            return  # Already running

        def _monitor():
            try:
                proc = subprocess.Popen(
                    ['pactl', 'subscribe'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                self._volume_monitor = proc
                for line in proc.stdout:
                    if "'change' on sink " in line and self.virtual_sink in line:
                        self._sync_volume()
            except (OSError, ValueError):
                pass
            finally:
                self._volume_monitor = None

        threading.Thread(target=_monitor, daemon=True).start()

    def stop_volume_sync(self):
        """Stop the volume monitor."""
        if self._volume_monitor:
            try:
                self._volume_monitor.terminate()
            except OSError:
                pass
            self._volume_monitor = None

    def _sync_volume(self):
        """Mirror audio_master volume/mute to all real output sinks."""
        # Get master volume
        output = run_cmd('pactl', 'get-sink-volume', self.virtual_sink, capture=True)
        mute_out = run_cmd('pactl', 'get-sink-mute', self.virtual_sink, capture=True)

        if not output:
            return

        # Extract volume percentage (e.g. "65%")
        vol_match = re.search(r'(\d+%)', output)
        if not vol_match:
            return
        volume = vol_match.group(1)
        mute = 'yes' in (mute_out or '')

        # Skip if unchanged (avoid feedback loops)
        mute_str = 'yes' if mute else 'no'
        if volume == self._last_volume and mute_str == self._last_mute:
            return
        self._last_volume = volume
        self._last_mute = mute_str

        # Apply to all real sinks
        jack_sink = config.get("jack_sink", "")
        if jack_sink:
            run_cmd('pactl', 'set-sink-volume', jack_sink, volume)
            run_cmd('pactl', 'set-sink-mute', jack_sink, mute_str)

        # Apply to BT sink
        self._refresh_state()
        if self._state.bt_sink_name:
            run_cmd('pactl', 'set-sink-volume', self._state.bt_sink_name, volume)
            run_cmd('pactl', 'set-sink-mute', self._state.bt_sink_name, mute_str)

    def cleanup(self):
        """Remove only modules created by this app."""
        self.stop_volume_sync()
        with self._lock:
            for mid in list(self._own_module_ids):
                run_cmd('pactl', 'unload-module', str(mid))
            self._own_module_ids.clear()

            modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
            if modules_output:
                mid = find_module_id(modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}')
                if mid:
                    run_cmd('pactl', 'unload-module', str(mid))

                for block in parse_module_blocks(modules_output):
                    if block['name'] == 'module-loopback' and f'{self.virtual_sink}.monitor' in block['argument']:
                        if block['id'] is not None:
                            run_cmd('pactl', 'unload-module', str(block['id']))

            time.sleep(0.3)

    def move_streams_to_master(self):
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.virtual_sink)

    def _reload_config(self):
        """Reload bt speaker name from config."""
        self.bt_speaker_name = config.get("bt_speaker_name", "")


# Global backend instance
audio = AudioBackend()
