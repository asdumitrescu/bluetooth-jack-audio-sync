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
        
        # Analog/Jack (Multiple)
        self.analog_sinks: List[str] = []
        # Map sink_name -> (module_id, delay)
        self.jack_loopbacks: dict[str, Tuple[int, int]] = {}
        
        # Bluetooth (Multiple)
        self.bt_sinks: List[str] = []
        # Map sink_name -> module_id
        self.bt_loopbacks: dict[str, int] = {}
        
        self.default_sink: Optional[str] = None


class AudioBackend:
    """
    Idempotent audio backend for PulseAudio.
    Only makes changes when necessary.
    """
    
    def __init__(self):
        self.virtual_sink = config.get("virtual_sink")
        # We process these but rely more on auto-detection now
        self.jack_sink = config.get("jack_sink") 
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
            state.bt_sinks = self._detect_all_bt_sinks(sinks_output)
            state.analog_sinks = self._detect_all_analog_sinks(sinks_output)
        
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
            
            # Find jack loopbacks for ALL detected analog sinks
            for sink in state.analog_sinks:
                loop = self._find_loopback_info(modules_output, sink)
                if loop:
                    state.jack_loopbacks[sink] = loop
            
            # Find BT loopbacks for ALL detected BT sinks
            for sink in state.bt_sinks:
                loop = self._find_loopback_info(modules_output, sink)
                if loop:
                    state.bt_loopbacks[sink] = loop[0] # just module_id
        
        self._state = state

    def _detect_all_bt_sinks(self, sinks_output: str) -> List[str]:
        """Detect all connected Bluetooth sinks."""
        bt_sinks = []
        for line in sinks_output.splitlines():
            # Look for bluez_sink
            if 'bluez_sink' in line:
                parts = line.split('\t')
                if parts:
                    bt_sinks.append(parts[1]) # The name is the second column
        return bt_sinks

    def _detect_all_analog_sinks(self, sinks_output: str) -> List[str]:
        """Detect all analog sinks (excluding our virtual master)."""
        analog_sinks = []
        for line in sinks_output.splitlines():
            # Look for typical analog keywords
            if 'analog' in line or 'pci' in line:
                 parts = line.split('\t')
                 if parts:
                    name = parts[1]
                    # Exclude our own virtual sink and potential monitor sources listed as sinks (rare but safe)
                    if name != self.virtual_sink and 'bluez' not in name:
                         analog_sinks.append(name)
        return analog_sinks
    
    def _find_bt_sink(self) -> Optional[str]:
        """Deprecated: Use _detect_all_bt_sinks instead."""
        # Kept for compatibility if needed, but implementation updated to use new logic
        sinks = self._detect_all_bt_sinks(run_cmd('pactl', 'list', 'sinks', 'short', capture=True))
        return sinks[0] if sinks else None
    
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
        
        # 1. Virtual sink must exist and be default
        if not self._state.virtual_sink_exists or self._state.default_sink != self.virtual_sink:
            return False
            
        # 2. All detected analog sinks must have a loopback
        for sink in self._state.analog_sinks:
            if sink not in self._state.jack_loopbacks:
                return False
                
        # 3. All detected bluetooth sinks must have a loopback
        for sink in self._state.bt_sinks:
            if sink not in self._state.bt_loopbacks:
                return False
                
        return True
    
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
        
        # 3. Create/update jack loopbacks for ALL analog sinks
        for sink in self._state.analog_sinks:
            if sink not in self._state.jack_loopbacks:
                run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={sink}',
                    f'latency_msec={jack_delay_ms}'
                )
                messages.append(f"Connected Analog: {sink}")
            else:
                # Check delay
                mod_id, current_delay = self._state.jack_loopbacks[sink]
                if current_delay != jack_delay_ms:
                     run_cmd('pactl', 'unload-module', str(mod_id))
                     time.sleep(0.1)
                     run_cmd(
                        'pactl', 'load-module', 'module-loopback',
                        f'source={self.virtual_sink}.monitor',
                        f'sink={sink}',
                        f'latency_msec={jack_delay_ms}'
                    )
                     messages.append(f"Updated delay for {sink}")

        
        # 4. Attach ALL Bluetooth speakers
        self._refresh_state() # Refresh to be safe
        for sink in self._state.bt_sinks:
            if sink not in self._state.bt_loopbacks:
                run_cmd('pactl', 'set-sink-mute', sink, '0')
                run_cmd('pactl', 'set-sink-volume', sink, '100%')
                run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={sink}',
                    'latency_msec=1'
                )
                messages.append(f"Connected Bluetooth: {sink}")
        
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
        """Update loopback delay for ALL analog sinks."""
        self._refresh_state()
        
        # We need to update ALL analog loopbacks
        for sink, (mod_id, _) in self._state.jack_loopbacks.items():
            run_cmd('pactl', 'unload-module', str(mod_id))
            time.sleep(0.05) # Small pause
            
            run_cmd(
                'pactl', 'load-module', 'module-loopback',
                f'source={self.virtual_sink}.monitor',
                f'sink={sink}',
                f'latency_msec={delay_ms}'
            )

    def set_jack_delay(self, delay_ms: int) -> bool:
        """Set Jack delay without full reset."""
        delay_ms = max(0, min(300, delay_ms))
        config.jack_delay = delay_ms
        
        self._refresh_state()
        if self._state.jack_loopbacks:
            self._update_jack_delay(delay_ms)
            return True
        return False
    
    def get_jack_delay(self) -> int:
        """Get current Jack delay."""
        self._refresh_state()
        # Return the delay of the first analog loopback found, or config default
        if self._state.jack_loopbacks:
            return list(self._state.jack_loopbacks.values())[0][1]
        return config.jack_delay
    
    def get_bt_latency(self) -> Optional[int]:
        """Get Bluetooth sink latency in ms (first one found)."""
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
