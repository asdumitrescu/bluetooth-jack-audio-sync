#!/usr/bin/env python3
"""
Configuration management for Audio Sync Master.
Handles settings persistence in ~/.config/audiosync/
Supports per-device delay configuration.
"""

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "audiosync"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Default delay for analog devices (ms) — compensates for Bluetooth latency
DEFAULT_ANALOG_DELAY = 121
# Default delay for Bluetooth devices (ms) — minimal, BT has its own buffer
DEFAULT_BT_DELAY = 1

# Default configuration
DEFAULTS = {
    "default_analog_delay_ms": DEFAULT_ANALOG_DELAY,
    "default_bt_delay_ms": DEFAULT_BT_DELAY,
    "device_delays": {},  # Per-device overrides: { "sink_name": delay_ms }
    "virtual_sink": "audio_master",
    "eq_enabled": False,
    "eq_bands": {
        "31": 0, "63": 0, "125": 0, "250": 0, "500": 0,
        "1k": 0, "2k": 0, "4k": 0, "8k": 0, "16k": 0
    },
    "eq_preset": "flat",
    "minimize_to_tray": True,
    "autostart": False,
    "eq_jack_enabled": False,
    "eq_bt_enabled": True
}

# EQ Presets
EQ_PRESETS = {
    "flat": {"31": 0, "63": 0, "125": 0, "250": 0, "500": 0, "1k": 0, "2k": 0, "4k": 0, "8k": 0, "16k": 0},
    "bass_boost": {"31": 8, "63": 6, "125": 4, "250": 2, "500": 0, "1k": 0, "2k": 0, "4k": 0, "8k": 0, "16k": 0},
    "treble_boost": {"31": 0, "63": 0, "125": 0, "250": 0, "500": 0, "1k": 1, "2k": 3, "4k": 5, "8k": 6, "16k": 8},
    "vocal": {"31": -2, "63": -1, "125": 0, "250": 2, "500": 4, "1k": 4, "2k": 3, "4k": 2, "8k": 1, "16k": 0},
    "rock": {"31": 5, "63": 4, "125": 3, "250": 1, "500": -1, "1k": -1, "2k": 1, "4k": 3, "8k": 4, "16k": 5},
    "electronic": {"31": 6, "63": 5, "125": 2, "250": 0, "500": -2, "1k": 0, "2k": 2, "4k": 4, "8k": 5, "16k": 6},
    "jazz": {"31": 3, "63": 2, "125": 1, "250": 2, "500": -1, "1k": -1, "2k": 0, "4k": 1, "8k": 2, "16k": 3},
    "classical": {"31": 4, "63": 3, "125": 2, "250": 1, "500": 0, "1k": 0, "2k": 0, "4k": 2, "8k": 3, "16k": 4},
    "hip_hop": {"31": 7, "63": 6, "125": 4, "250": 2, "500": 1, "1k": 0, "2k": 1, "4k": 2, "8k": 3, "16k": 2},
    "loudness": {"31": 6, "63": 4, "125": 2, "250": 0, "500": -2, "1k": -2, "2k": 0, "4k": 2, "8k": 4, "16k": 6}
}


class Config:
    """Configuration manager with persistence."""

    def __init__(self):
        self._config: dict = {}
        self._load()

    def _ensure_dir(self):
        """Create config directory if it doesn't exist."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self):
        """Load configuration from file, using defaults for missing values."""
        self._config = DEFAULTS.copy()
        self._config["device_delays"] = {}
        self._config["eq_bands"] = DEFAULTS["eq_bands"].copy()

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    saved = json.load(f)
                    for key, value in saved.items():
                        if isinstance(value, dict) and key in self._config and isinstance(self._config[key], dict):
                            self._config[key] = {**self._config[key], **value}
                        else:
                            self._config[key] = value
            except (json.JSONDecodeError, IOError):
                pass

        # Migrate from old single-device config
        if "jack_delay_ms" in self._config and "default_analog_delay_ms" not in self._config:
            self._config["default_analog_delay_ms"] = self._config["jack_delay_ms"]

    def save(self):
        """Persist configuration to file."""
        self._ensure_dir()
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self._config, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any, save: bool = True):
        """Set a configuration value."""
        self._config[key] = value
        if save:
            self.save()

    # --- Per-device delay management ---

    def get_device_delay(self, sink_name: str) -> int:
        """Get the configured delay for a specific device.
        Falls back to default based on device type (analog vs bluetooth)."""
        delays = self._config.get("device_delays", {})
        if sink_name in delays:
            return delays[sink_name]

        # Determine default based on device type
        if 'bluez_sink' in sink_name or 'bluez_output' in sink_name:
            return self._config.get("default_bt_delay_ms", DEFAULT_BT_DELAY)
        return self._config.get("default_analog_delay_ms", DEFAULT_ANALOG_DELAY)

    def set_device_delay(self, sink_name: str, delay_ms: int):
        """Set delay for a specific device and persist."""
        if "device_delays" not in self._config:
            self._config["device_delays"] = {}
        self._config["device_delays"][sink_name] = max(1, min(500, delay_ms))
        self.save()

    def set_default_analog_delay(self, delay_ms: int):
        """Set the default delay for analog devices."""
        self._config["default_analog_delay_ms"] = max(1, min(500, delay_ms))
        self.save()

    def set_default_bt_delay(self, delay_ms: int):
        """Set the default delay for Bluetooth devices."""
        self._config["default_bt_delay_ms"] = max(1, min(500, delay_ms))
        self.save()

    # --- Per-device offset management (analog only) ---

    def get_device_offset(self, sink_name: str) -> int:
        """Get the configured offset for an analog device (default 0)."""
        offsets = self._config.get("device_offsets", {})
        return offsets.get(sink_name, 0)

    def set_device_offset(self, sink_name: str, offset_ms: int):
        """Set offset for an analog device and persist."""
        if "device_offsets" not in self._config:
            self._config["device_offsets"] = {}
        self._config["device_offsets"][sink_name] = max(-150, min(150, offset_ms))
        self.save()

    # --- EQ management ---

    def get_eq_bands(self) -> dict:
        """Get current EQ band values."""
        return self._config.get("eq_bands", EQ_PRESETS["flat"]).copy()

    def set_eq_bands(self, bands: dict, save: bool = True):
        """Set EQ band values."""
        self._config["eq_bands"] = bands
        if save:
            self.save()

    def apply_preset(self, preset_name: str, save: bool = True):
        """Apply an EQ preset."""
        if preset_name in EQ_PRESETS:
            self._config["eq_bands"] = EQ_PRESETS[preset_name].copy()
            self._config["eq_preset"] = preset_name
            if save:
                self.save()
            return True
        return False

    # --- Legacy compatibility ---

    @property
    def jack_delay(self) -> int:
        """Legacy: get default analog delay."""
        return self._config.get("default_analog_delay_ms", DEFAULT_ANALOG_DELAY)

    @jack_delay.setter
    def jack_delay(self, value: int):
        """Legacy: set default analog delay."""
        self._config["default_analog_delay_ms"] = max(0, min(500, value))
        self.save()


# Global config instance
config = Config()
