#!/usr/bin/env python3
"""
Equalizer backend using PulseAudio LADSPA module with mbeq plugin.
Provides 15-band parametric EQ without external dependencies.
"""

import threading
from pathlib import Path
from typing import Optional, Dict, List
from config import config, EQ_PRESETS
from pulse_utils import (
    run_cmd, find_module_id, parse_module_blocks,
    list_movable_sink_inputs,
)

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

# Common LADSPA plugin search paths
LADSPA_SEARCH_PATHS = [
    Path('/usr/lib/ladspa'),
    Path('/usr/lib64/ladspa'),
    Path('/usr/local/lib/ladspa'),
    Path('/usr/lib/x86_64-linux-gnu/ladspa'),
    Path('/usr/lib/aarch64-linux-gnu/ladspa'),
]


class Equalizer:
    """
    10-band equalizer using PulseAudio module-ladspa-sink with mbeq plugin.
    Thread-safe with proper cleanup on failure.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.sink_name = "eq_sink"
        self.master_sink = config.get("virtual_sink", "audio_master")
        self._module_id: Optional[int] = None
        self._enabled = config.get("eq_enabled", False)
        self._bands = config.get_eq_bands()
        # PipeWire needs longer settle time after module changes
        from pulse_utils import is_pipewire
        self._settle = 0.5 if is_pipewire() else 0.2

    def is_ladspa_available(self) -> bool:
        """Check if LADSPA mbeq plugin is available using pathlib (no subprocess)."""
        for search_path in LADSPA_SEARCH_PATHS:
            if not search_path.exists():
                continue
            for f in search_path.iterdir():
                if 'mbeq' in f.name:
                    return True
        return False

    def _find_eq_module(self) -> Optional[int]:
        """Find existing EQ module ID (block-safe parsing)."""
        output = run_cmd('pactl', 'list', 'modules', capture=True)
        if output:
            return find_module_id(output, 'module-ladspa-sink', f'sink_name={self.sink_name}')
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
        with self._lock:
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
                import time
                time.sleep(self._settle)
                self._enabled = True
                self._module_id = self._find_eq_module()
                run_cmd('pactl', 'set-default-sink', self.sink_name)
                time.sleep(0.1)
                self._move_streams_to_eq()
                config.set("eq_enabled", True)
                return True

            return False

    def _move_streams_to_eq(self):
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.sink_name)

    def disable(self) -> bool:
        """Disable the equalizer."""
        with self._lock:
            import time
            run_cmd('pactl', 'set-default-sink', self.master_sink)
            time.sleep(0.1)
            self._move_streams_to_master()

            module_id = self._find_eq_module()
            if module_id:
                run_cmd('pactl', 'unload-module', str(module_id))
                time.sleep(self._settle)

            self._enabled = False
            self._module_id = None
            config.set("eq_enabled", False)
            return True

    def _move_streams_to_master(self):
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, self.master_sink)

    def is_enabled(self) -> bool:
        return self._find_eq_module() is not None

    def set_band(self, band: str, value: int) -> bool:
        """Set a single EQ band value."""
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
        return self._bands.copy()

    def _apply_bands(self) -> bool:
        """Apply current band values to the LADSPA sink."""
        import time
        with self._lock:
            self._move_streams_to_master()
            run_cmd('pactl', 'set-default-sink', self.master_sink)

            module_id = self._find_eq_module()
            if module_id:
                run_cmd('pactl', 'unload-module', str(module_id))
                time.sleep(self._settle)

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
                time.sleep(self._settle)
                self._module_id = self._find_eq_module()
                run_cmd('pactl', 'set-default-sink', self.sink_name)
                time.sleep(0.1)
                self._move_streams_to_eq()
                return True

            # Reload failed — mark EQ as disabled to avoid degenerate loop
            self._enabled = False
            self._module_id = None
            config.set("eq_enabled", False)
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
        return list(EQ_PRESETS.keys())

    def reset(self) -> bool:
        return self.apply_preset("flat")


# Global equalizer instance
equalizer = Equalizer()
