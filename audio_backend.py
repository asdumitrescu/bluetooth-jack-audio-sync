#!/usr/bin/env python3
"""
Audio Backend - Idempotent PulseAudio control for Audio Sync Master.
Manages virtual sinks, loopbacks, and delay without breaking existing setups.
"""

import subprocess
import re
import time
from typing import Optional, Tuple, List
from config import config


def run_cmd(*args, capture: bool = False) -> str | bool:
    """Run a shell command safely."""
    try:
        if capture:
            result = subprocess.run(
                list(args), 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            return result.stdout
        subprocess.run(
            list(args), 
            check=True, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return "" if capture else False


def _find_loopback_module_ids() -> set[int]:
    output = run_cmd('pactl', 'list', 'modules', capture=True)
    if not output:
        return set()
    ids: set[int] = set()
    for match in re.finditer(r'Module #(\d+)\s+Name: module-loopback', output):
        ids.add(int(match.group(1)))
    return ids


def _list_movable_sink_inputs() -> List[str]:
    """List sink inputs excluding internal loopbacks to keep sync stable."""
    output = run_cmd('pactl', 'list', 'sink-inputs', capture=True)
    if not output:
        return []
    loopback_modules = _find_loopback_module_ids()
    inputs: List[str] = []
    for block in output.split('Sink Input #')[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        input_id = lines[0].strip()
        owner_module = None
        driver = None
        app_name = None
        media_name = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('Owner Module:'):
                owner_module = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('Driver:'):
                driver = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('application.name ='):
                app_name = stripped.split('=', 1)[1].strip().strip('"')
            elif stripped.startswith('media.name ='):
                media_name = stripped.split('=', 1)[1].strip().strip('"')
        if owner_module and owner_module.isdigit() and int(owner_module) in loopback_modules:
            continue
        if driver and 'module-loopback' in driver:
            continue
        if app_name in ('module-loopback', 'module-ladspa-sink'):
            continue
        if media_name and 'Loopback' in media_name:
            continue
        inputs.append(input_id)
    return inputs


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
        self.jack_sink_available: bool = False
        self.default_sink: Optional[str] = None


class AudioBackend:
    """
    Idempotent audio backend for PulseAudio.
    Only makes changes when necessary.
    """
    
    def __init__(self):
        self.virtual_sink = config.get("virtual_sink")
        self.jack_sink = config.get("jack_sink")
        self.jack_card = config.get("jack_card")
        self.jack_port = config.get("jack_port")
        self.bt_speaker_name = config.get("bt_speaker_name")
        self._state = AudioState()
    
    def get_state(self) -> AudioState:
        """Refresh and return current audio state."""
        self._refresh_state()
        return self._state
    
    def _refresh_state(self):
        """Query PulseAudio for current state."""
        state = AudioState()
        
        # Check sinks
        sinks_output = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if sinks_output:
            state.virtual_sink_exists = self.virtual_sink in sinks_output
            state.jack_sink_available = self.jack_sink in sinks_output
            state.bt_sink_name = self._find_bt_sink()
            state.bt_connected = state.bt_sink_name is not None
        
        # Check default sink
        info_output = run_cmd('pactl', 'info', capture=True)
        if info_output:
            match = re.search(r'Default Sink: (\S+)', info_output)
            if match:
                state.default_sink = match.group(1)
        
        # Check modules for loopbacks and virtual sink
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if modules_output:
            state.virtual_sink_module_id = self._find_module_id(
                modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}'
            )
            state.virtual_sink_exists = state.virtual_sink_module_id is not None
            
            # Find jack loopback
            jack_loop = self._find_loopback_info(modules_output, self.jack_sink)
            if jack_loop:
                state.jack_loopback_exists = True
                state.jack_loopback_module_id = jack_loop[0]
                state.jack_loopback_delay = jack_loop[1]
            
            # Find BT loopback
            if state.bt_sink_name:
                bt_loop = self._find_loopback_info(modules_output, state.bt_sink_name)
                if bt_loop:
                    state.bt_loopback_exists = True
                    state.bt_loopback_module_id = bt_loop[0]
        
        self._state = state
    
    def _find_bt_sink(self) -> Optional[str]:
        """Find Bluetooth sink by speaker name."""
        try:
            output = run_cmd('pactl', 'list', 'sinks', capture=True)
            for block in output.split('Sink #'):
                if self.bt_speaker_name in block:
                    match = re.search(r'Name: (bluez_sink\.\S+)', block)
                    if match:
                        return match.group(1)
        except Exception:
            pass
        return None
    
    def _find_module_id(self, modules_output: str, module_name: str, arg_match: str) -> Optional[int]:
        """Find module ID by name and argument."""
        pattern = rf'Module #(\d+)\s+Name: {module_name}.*?Argument: ([^\n]+)'
        for match in re.finditer(pattern, modules_output, re.DOTALL):
            if arg_match in match.group(2):
                return int(match.group(1))
        return None
    
    def _find_loopback_info(self, modules_output: str, sink_name: str) -> Optional[Tuple[int, int]]:
        """Find loopback module ID and latency for a sink."""
        pattern = r'Module #(\d+)\s+Name: module-loopback.*?Argument: ([^\n]+)'
        for match in re.finditer(pattern, modules_output, re.DOTALL):
            args = match.group(2)
            if f'sink={sink_name}' in args and f'source={self.virtual_sink}.monitor' in args:
                latency_match = re.search(r'latency_msec=(\d+)', args)
                latency = int(latency_match.group(1)) if latency_match else 10
                return (int(match.group(1)), latency)
        return None
    
    def is_setup_complete(self) -> bool:
        """Check if audio sync is fully configured."""
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
        if jack_delay_ms is None:
            jack_delay_ms = config.jack_delay
        
        self._refresh_state()
        messages = []
        
        # 1. Create virtual sink if missing
        if not self._state.virtual_sink_exists:
            result = run_cmd(
                'pactl', 'load-module', 'module-null-sink',
                f'sink_name={self.virtual_sink}',
                f'sink_properties=device.description="Audio_Master"'
            )
            if result:
                messages.append("Created virtual master sink")
                time.sleep(0.3)
            else:
                return (False, "Failed to create virtual sink")
        
        # 2. Set as default sink if not already
        self._refresh_state()
        if self._state.default_sink != self.virtual_sink:
            run_cmd('pactl', 'set-default-sink', self.virtual_sink)
            messages.append("Set audio_master as default")
        
        # 3. Create/update jack loopback
        if not self._state.jack_loopback_exists:
            run_cmd(
                'pactl', 'load-module', 'module-loopback',
                f'source={self.virtual_sink}.monitor',
                f'sink={self.jack_sink}',
                f'latency_msec={jack_delay_ms}'
            )
            messages.append(f"Created Jack loopback ({jack_delay_ms}ms)")
        elif self._state.jack_loopback_delay != jack_delay_ms:
            # Need to recreate with new delay
            self._update_jack_delay(jack_delay_ms)
            messages.append(f"Updated Jack delay to {jack_delay_ms}ms")
        
        # 4. Attach Bluetooth if connected
        self._refresh_state()
        if self._state.bt_connected and not self._state.bt_loopback_exists:
            run_cmd('pactl', 'set-sink-mute', self._state.bt_sink_name, '0')
            run_cmd('pactl', 'set-sink-volume', self._state.bt_sink_name, '100%')
            run_cmd(
                'pactl', 'load-module', 'module-loopback',
                f'source={self.virtual_sink}.monitor',
                f'sink={self._state.bt_sink_name}',
                'latency_msec=1'
            )
            messages.append("Attached Bluetooth speaker")
        
        if not messages:
            return (True, "Audio sync already configured correctly")
        
        # 5. Restore EQ routing if EQ was enabled
        self._restore_eq_routing()
        
        return (True, "; ".join(messages))
    
    def _restore_eq_routing(self):
        """If EQ is enabled, make sure audio routes through it."""
        # Check if eq_sink exists
        sinks = run_cmd('pactl', 'list', 'sinks', 'short', capture=True)
        if 'eq_sink' in sinks:
            # EQ exists, set it as default and move streams
            run_cmd('pactl', 'set-default-sink', 'eq_sink')
            try:
                for input_id in _list_movable_sink_inputs():
                    run_cmd('pactl', 'move-sink-input', input_id, 'eq_sink')
            except Exception:
                pass
    
    def _update_jack_delay(self, delay_ms: int):
        """Update jack loopback delay by recreating it."""
        self._refresh_state()
        
        # Remove old loopback
        if self._state.jack_loopback_module_id:
            run_cmd('pactl', 'unload-module', str(self._state.jack_loopback_module_id))
            time.sleep(0.1)
        
        # Create new with updated delay
        run_cmd(
            'pactl', 'load-module', 'module-loopback',
            f'source={self.virtual_sink}.monitor',
            f'sink={self.jack_sink}',
            f'latency_msec={delay_ms}'
        )
    
    def set_jack_delay(self, delay_ms: int) -> bool:
        """Set Jack delay without full reset."""
        delay_ms = max(0, min(300, delay_ms))
        config.jack_delay = delay_ms
        
        self._refresh_state()
        if self._state.jack_loopback_exists:
            self._update_jack_delay(delay_ms)
            return True
        return False
    
    def get_jack_delay(self) -> int:
        """Get current Jack delay."""
        self._refresh_state()
        if self._state.jack_loopback_delay is not None:
            return self._state.jack_loopback_delay
        return config.jack_delay
    
    def get_bt_latency(self) -> Optional[int]:
        """Get Bluetooth sink latency in ms."""
        try:
            output = run_cmd('pactl', 'list', 'sinks', capture=True)
            for block in output.split('Sink #'):
                if 'bluez_sink' in block:
                    match = re.search(r'Latency:\s+(\d+)\s+usec', block)
                    if match:
                        return int(match.group(1)) // 1000
        except Exception:
            pass
        return None
    
    def cleanup(self):
        """Remove all audio sync modules."""
        run_cmd('pactl', 'unload-module', 'module-loopback')
        run_cmd('pactl', 'unload-module', 'module-null-sink')
        time.sleep(0.3)
    
    def move_streams_to_master(self):
        """Move app audio streams to virtual master."""
        try:
            for input_id in _list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, self.virtual_sink)
        except Exception:
            pass


# Global backend instance
audio = AudioBackend()
