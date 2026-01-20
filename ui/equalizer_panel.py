#!/usr/bin/env python3
"""
Equalizer Panel - 10-band parametric EQ with presets.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from equalizer import equalizer
from config import config, EQ_PRESETS


# EQ band frequencies
BANDS = ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]
BAND_LABELS = ["31Hz", "63Hz", "125Hz", "250Hz", "500Hz", "1kHz", "2kHz", "4kHz", "8kHz", "16kHz"]


class EQSlider(Gtk.Box):
    """Single EQ band slider (vertical)."""
    
    def __init__(self, band: str, label: str, on_change):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class('eq-slider')
        
        self.band = band
        self._on_change = on_change
        self._updating = False
        
        # dB value label
        self.value_label = Gtk.Label(label="0")
        self.value_label.set_css_classes(['eq-value'])
        self.append(self.value_label)
        
        # Vertical slider
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -12, 12, 1)
        self.slider.set_inverted(True)  # +12 at top
        self.slider.set_value(0)
        self.slider.set_draw_value(False)
        self.slider.set_vexpand(True)
        self.slider.connect('value-changed', self._on_slider_changed)
        self.append(self.slider)
        
        # Frequency label
        freq_label = Gtk.Label(label=label)
        freq_label.set_css_classes(['eq-label'])
        self.append(freq_label)
    
    def _on_slider_changed(self, slider):
        """Handle slider change."""
        if self._updating:
            return
        value = int(slider.get_value())
        sign = "+" if value >= 0 else ""
        self.value_label.set_text(f"{sign}{value}")
        self._on_change(self.band, value)
    
    def set_value(self, value: int):
        """Set slider value without triggering callback."""
        self._updating = True
        self.slider.set_value(value)
        sign = "+" if value >= 0 else ""
        self.value_label.set_text(f"{sign}{value}")
        self._updating = False
    
    def get_value(self) -> int:
        return int(self.slider.get_value())


class EqualizerPanel(Gtk.Box):
    """10-band parametric equalizer panel."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.add_css_class('glass-card')
        
        self.sliders: dict[str, EQSlider] = {}
        self._setup_ui()
        self._load_values()
    
    def _setup_ui(self):
        """Build the equalizer UI."""
        
        # Title row with enable switch
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        icon = Gtk.Label(label="🎛️")
        icon.set_css_classes(['title-label'])
        title_row.append(icon)
        
        title = Gtk.Label(label="10-Band Parametric Equalizer")
        title.set_css_classes(['title-label'])
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)
        title_row.append(title)
        
        enable_label = Gtk.Label(label="Enable")
        enable_label.set_css_classes(['subtitle-label'])
        title_row.append(enable_label)
        
        self.enable_switch = Gtk.Switch()
        self.enable_switch.set_active(equalizer.is_enabled())
        self.enable_switch.connect('state-set', self._on_enable_toggled)
        title_row.append(self.enable_switch)
        
        self.append(title_row)
        
        # Presets row
        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preset_row.set_margin_top(8)
        
        preset_label = Gtk.Label(label="Presets:")
        preset_label.set_css_classes(['subtitle-label'])
        preset_row.append(preset_label)
        
        # Scrollable preset buttons
        preset_scroll = Gtk.ScrolledWindow()
        preset_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        preset_scroll.set_hexpand(True)
        preset_scroll.set_max_content_width(500)
        
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        presets = [
            ("flat", "Flat"),
            ("bass_boost", "Bass"),
            ("treble_boost", "Treble"),
            ("vocal", "Vocal"),
            ("rock", "Rock"),
            ("electronic", "Electronic"),
            ("jazz", "Jazz"),
            ("classical", "Classical"),
            ("hip_hop", "Hip Hop"),
            ("loudness", "Loudness")
        ]
        
        self.preset_buttons = {}
        for preset_id, preset_name in presets:
            btn = Gtk.ToggleButton(label=preset_name)
            btn.set_css_classes(['preset-button'])
            btn.connect('toggled', self._on_preset_clicked, preset_id)
            self.preset_buttons[preset_id] = btn
            preset_box.append(btn)
        
        preset_scroll.set_child(preset_box)
        preset_row.append(preset_scroll)
        
        self.append(preset_row)
        
        # EQ sliders container
        eq_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        eq_container.add_css_class('glass-card-inner')
        eq_container.set_margin_top(12)
        
        # dB scale labels
        db_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        db_row.set_margin_start(20)
        
        for db in ["+12", "+6", "0", "-6", "-12"]:
            label = Gtk.Label(label=db)
            label.set_css_classes(['eq-label'])
            label.set_vexpand(True)
        
        # Sliders row
        sliders_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sliders_box.set_halign(Gtk.Align.CENTER)
        sliders_box.set_homogeneous(True)
        
        for band, label in zip(BANDS, BAND_LABELS):
            slider = EQSlider(band, label, self._on_band_changed)
            self.sliders[band] = slider
            sliders_box.append(slider)
        
        eq_container.append(sliders_box)
        self.append(eq_container)
        
        # Control buttons row
        ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ctrl_row.set_halign(Gtk.Align.CENTER)
        ctrl_row.set_margin_top(12)
        
        reset_btn = Gtk.Button(label="Reset (Flat)")
        reset_btn.connect('clicked', lambda b: self._apply_preset('flat'))
        ctrl_row.append(reset_btn)
        
        save_btn = Gtk.Button(label="Save Custom")
        save_btn.set_css_classes(['suggested-action'])
        save_btn.connect('clicked', self._on_save_custom)
        ctrl_row.append(save_btn)
        
        self.append(ctrl_row)
        
        # Status label
        self.status_label = Gtk.Label(label="")
        self.status_label.set_css_classes(['status-label'])
        self.status_label.set_margin_top(8)
        self.append(self.status_label)
    
    def _load_values(self):
        """Load saved EQ values."""
        bands = equalizer.get_bands()
        for band, value in bands.items():
            if band in self.sliders:
                self.sliders[band].set_value(value)
        
        # Highlight active preset
        current_preset = config.get('eq_preset', 'flat')
        self._update_preset_buttons(current_preset)
    
    def _on_enable_toggled(self, switch, state) -> bool:
        """Handle EQ enable/disable."""
        if state:
            if equalizer.enable():
                self._show_status("Equalizer enabled")
            else:
                self._show_status("Failed to enable EQ (LADSPA plugin may be missing)")
                switch.set_active(False)
                return True
        else:
            equalizer.disable()
            self._show_status("Equalizer disabled")
        return False
    
    def _on_band_changed(self, band: str, value: int):
        """Handle individual band change."""
        equalizer.set_band(band, value)
        self._update_preset_buttons(None)  # Clear preset selection
    
    def _on_preset_clicked(self, button, preset_id: str):
        """Handle preset button click."""
        if button.get_active():
            self._apply_preset(preset_id)
    
    def _apply_preset(self, preset_id: str):
        """Apply EQ preset."""
        equalizer.apply_preset(preset_id)
        
        # Update sliders
        bands = EQ_PRESETS[preset_id]
        for band, value in bands.items():
            if band in self.sliders:
                self.sliders[band].set_value(value)
        
        self._update_preset_buttons(preset_id)
        self._show_status(f"Applied preset: {preset_id.replace('_', ' ').title()}")
    
    def _update_preset_buttons(self, active_id: str | None):
        """Update preset button states."""
        for preset_id, btn in self.preset_buttons.items():
            btn.handler_block_by_func(self._on_preset_clicked)
            btn.set_active(preset_id == active_id)
            btn.handler_unblock_by_func(self._on_preset_clicked)
    
    def _on_save_custom(self, button):
        """Save current settings as custom."""
        bands = {band: slider.get_value() for band, slider in self.sliders.items()}
        equalizer.set_bands(bands)
        config.set('eq_preset', 'custom')
        self._show_status("Custom EQ settings saved")
    
    def _show_status(self, message: str):
        """Show temporary status message."""
        self.status_label.set_text(message)
        GLib.timeout_add_seconds(3, lambda: self.status_label.set_text(""))
