#!/usr/bin/env python3
"""
Equalizer backend using PulseAudio LADSPA module with mbeq plugin.
Provides 15-band parametric EQ without external dependencies.
"""

import subprocess
import re
import time
from typing import Optional, Dict, List
from config import config, EQ_PRESETS
from audio_backend import run_cmd, list_movable_sink_inputs, audio as _audio_backend

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
        result = subprocess.run(
            ['find', '/usr/lib', '-name', 'mbeq_*', '-o', '-name', '*ladspa*mbeq*'],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())

    def _find_eq_module(self) -> Optional[int]:
        """Find existing EQ module ID (block-safe)."""
        output = run_cmd('pactl', 'list', 'modules', capture=True)
        if not output:
            return None
        for block in output.split('Module #')[1:]:
            if 'module-ladspa-sink' in block and f'sink_name={self.sink_name}' in block:
                id_match = re.match(r'(\d+)', block.strip())
                if id_match:
                    return int(id_match.group(1))
        return None

    def _build_mbeq_controls(self) -> str:
        """Build mbeq control string from UI band values."""
        mbeq_values = [0.0] * 15
        for ui_band, value in self._bands.items():
            if ui_band in UI_TO_MBEQ:
                for mbeq_idx in UI_TO_MBEQ[ui_band]:
                    mbeq_values[mbeq_idx] = float(value)
        return ','.join(str(v) for v in mbeq_values)

    def enable(self) -> bool:
        """Enable the equalizer."""
        if self._find_eq_module():
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            self._enabled = True
            config.set("eq_enabled", True)
            return True

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
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            config.set("eq_enabled", True)
            _audio_backend.fix_loopback_routing()
            return True

        return False

    def _move_streams_to_eq(self):
        """Move app audio streams to EQ sink."""
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.sink_name)

    def disable(self) -> bool:
        """Disable the equalizer."""
        run_cmd('pactl', 'set-default-sink', self.master_sink)
        self._move_streams_to_master()

        module_id = self._find_eq_module()
        if module_id:
            run_cmd('pactl', 'unload-module', str(module_id))

        self._enabled = False
        self._module_id = None
        config.set("eq_enabled", False)
        _audio_backend.fix_loopback_routing()
        return True

    def _move_streams_to_master(self):
        """Move app audio streams back to master sink."""
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.master_sink)

    def is_enabled(self) -> bool:
        """Check if EQ is currently enabled."""
        return self._find_eq_module() is not None

    def set_band(self, band: str, value: int) -> bool:
        """Set a single EQ band value (-12 to +12 dB)."""
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
        self._move_streams_to_master()
        run_cmd('pactl', 'set-default-sink', self.master_sink)
        module_id = self._find_eq_module()
        if module_id:
            run_cmd('pactl', 'unload-module', str(module_id))
            time.sleep(0.3)

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
            run_cmd('pactl', 'set-default-sink', self.sink_name)
            self._move_streams_to_eq()
            _audio_backend.fix_loopback_routing()
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
