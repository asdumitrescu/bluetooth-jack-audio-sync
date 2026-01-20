#!/usr/bin/env python3
"""
Equalizer backend using PulseAudio LADSPA module with mbeq plugin.
Provides 15-band parametric EQ without external dependencies.
"""

import subprocess
import re
from typing import Optional, Dict, List
from config import config, EQ_PRESETS

# LADSPA mbeq frequency bands (Hz)
MBEQ_BANDS = [50, 100, 156, 220, 311, 440, 622, 880, 1250, 1750, 2500, 3500, 5000, 10000, 20000]

# Mapping from UI bands to mbeq bands (approximate)
UI_TO_MBEQ = {
    "31": [0],           # 50Hz
    "63": [1],           # 100Hz  
    "125": [2, 3],       # 156, 220Hz
    "250": [4],          # 311Hz
    "500": [5, 6],       # 440, 622Hz
    "1k": [7, 8],        # 880, 1250Hz
    "2k": [9],           # 1750Hz
    "4k": [10, 11],      # 2500, 3500Hz
    "8k": [12],          # 5000Hz
    "16k": [13, 14]      # 10000, 20000Hz
}


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
    except Exception:
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


class Equalizer:
    """
    10-band equalizer using PulseAudio module-ladspa-sink with mbeq plugin.
    """
    
    def __init__(self):
        self.sink_name = "eq_sink"
        self.master_sink = config.get("virtual_sink", "audio_master")
        self._module_id: Optional[int] = None
        self._enabled = config.get("eq_enabled", False)
        self._bands = config.get_eq_bands()
    
    def is_ladspa_available(self) -> bool:
        """Check if LADSPA mbeq plugin is available."""
        # Check for the plugin file
        result = subprocess.run(
            ['find', '/usr/lib', '-name', 'mbeq_*', '-o', '-name', '*ladspa*mbeq*'],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    
    def _find_eq_module(self) -> Optional[int]:
        """Find existing EQ module ID."""
        output = run_cmd('pactl', 'list', 'modules', capture=True)
        if output:
            pattern = rf'Module #(\d+)\s+Name: module-ladspa-sink.*?sink_name={self.sink_name}'
            match = re.search(pattern, output, re.DOTALL)
            if match:
                return int(match.group(1))
        return None
    
    def _build_mbeq_controls(self) -> str:
        """Build mbeq control string from UI band values."""
        # Start with all bands at 0
        mbeq_values = [0.0] * 15
        
        # Map UI bands to mbeq bands
        for ui_band, value in self._bands.items():
            if ui_band in UI_TO_MBEQ:
                for mbeq_idx in UI_TO_MBEQ[ui_band]:
                    mbeq_values[mbeq_idx] = float(value)
        
        # Format as control string
        return ','.join(str(v) for v in mbeq_values)
    
    def enable(self) -> bool:
        """Enable the equalizer."""
        if self._find_eq_module():
            # Already enabled, just make sure routing is correct
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            self._enabled = True
            config.set("eq_enabled", True)
            return True
        
        # Create LADSPA sink
        controls = self._build_mbeq_controls()
        
        result = run_cmd(
            'pactl', 'load-module', 'module-ladspa-sink',
            f'sink_name={self.sink_name}',
            f'sink_master={self.master_sink}',
            'plugin=mbeq_1197',
            'label=mbeq',
            f'control={controls}'
        )
        
        if result:
            self._enabled = True
            self._module_id = self._find_eq_module()
            # Set EQ sink as default so audio routes through it
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            config.set("eq_enabled", True)
            return True
        
        return False
    
    def _move_streams_to_eq(self):
        """Move app audio streams to EQ sink."""
        try:
            for input_id in _list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, self.sink_name)
        except Exception:
            pass
    
    def disable(self) -> bool:
        """Disable the equalizer."""
        # First restore default sink to audio_master
        run_cmd('pactl', 'set-default-sink', self.master_sink)
        self._move_streams_to_master()
        
        module_id = self._find_eq_module()
        if module_id:
            run_cmd('pactl', 'unload-module', str(module_id))
        
        self._enabled = False
        self._module_id = None
        config.set("eq_enabled", False)
        return True
    
    def _move_streams_to_master(self):
        """Move app audio streams back to master sink."""
        try:
            for input_id in _list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, self.master_sink)
        except Exception:
            pass
    
    def is_enabled(self) -> bool:
        """Check if EQ is currently enabled."""
        return self._find_eq_module() is not None
    
    def set_band(self, band: str, value: int) -> bool:
        """
        Set a single EQ band value.
        band: one of "31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"
        value: -12 to +12 dB
        """
        value = max(-12, min(12, value))
        self._bands[band] = value
        config.set_eq_bands(self._bands)
        
        if self._enabled:
            return self._apply_bands()
        return True
    
    def set_bands(self, bands: Dict[str, int]) -> bool:
        """Set all EQ bands at once."""
        for band, value in bands.items():
            self._bands[band] = max(-12, min(12, value))
        config.set_eq_bands(self._bands)
        
        if self._enabled:
            return self._apply_bands()
        return True
    
    def get_bands(self) -> Dict[str, int]:
        """Get current band values."""
        return self._bands.copy()
    
    def _apply_bands(self) -> bool:
        """Apply current band values to the LADSPA sink."""
        # Keep audio routed through the virtual master during reload.
        self._move_streams_to_master()
        run_cmd('pactl', 'set-default-sink', self.master_sink)
        module_id = self._find_eq_module()
        if module_id:
            run_cmd('pactl', 'unload-module', str(module_id))
        
        controls = self._build_mbeq_controls()
        
        result = run_cmd(
            'pactl', 'load-module', 'module-ladspa-sink',
            f'sink_name={self.sink_name}',
            f'sink_master={self.master_sink}',
            'plugin=mbeq_1197',
            'label=mbeq',
            f'control={controls}'
        )
        
        if result:
            self._module_id = self._find_eq_module()
            # Restore routing through eq_sink after reload.
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            return True
        
        return False
    
    def apply_preset(self, preset_name: str) -> bool:
        """Apply an EQ preset."""
        if preset_name in EQ_PRESETS:
            self._bands = EQ_PRESETS[preset_name].copy()
            config.apply_preset(preset_name)
            
            if self._enabled:
                return self._apply_bands()
            return True
        return False
    
    def get_presets(self) -> List[str]:
        """Get list of available presets."""
        return list(EQ_PRESETS.keys())
    
    def reset(self) -> bool:
        """Reset to flat EQ."""
        return self.apply_preset("flat")


# Global equalizer instance
equalizer = Equalizer()
