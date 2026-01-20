#!/usr/bin/env python3
"""
Delay Control Panel - UI component for adjusting Jack audio delay.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from audio_backend import audio
from config import config


class DelayPanel(Gtk.Box):
    """Panel for controlling Jack audio delay synchronization."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.add_css_class('glass-card')
        
        self._updating = False
        self._setup_ui()
        self._start_monitoring()
    
    def _setup_ui(self):
        """Build the delay control UI."""
        
        # Title section
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box.set_halign(Gtk.Align.START)
        
        icon = Gtk.Label(label="🎧")
        icon.set_css_classes(['title-label'])
        title_box.append(icon)
        
        title = Gtk.Label(label="Jack Audio Delay")
        title.set_css_classes(['title-label'])
        title_box.append(title)
        
        self.append(title_box)
        
        # Delay value display
        value_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        value_box.set_halign(Gtk.Align.CENTER)
        value_box.set_margin_top(8)
        value_box.set_margin_bottom(8)
        
        self.delay_label = Gtk.Label(label=str(config.jack_delay))
        self.delay_label.set_css_classes(['value-label'])
        value_box.append(self.delay_label)
        
        unit = Gtk.Label(label="ms")
        unit.set_css_classes(['unit-label'])
        unit.set_valign(Gtk.Align.END)
        unit.set_margin_bottom(4)
        value_box.append(unit)
        
        self.append(value_box)
        
        # Slider
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 300, 1)
        self.slider.set_value(config.jack_delay)
        self.slider.set_draw_value(False)
        self.slider.set_hexpand(True)
        self.slider.add_mark(0, Gtk.PositionType.BOTTOM, "0")
        self.slider.add_mark(115, Gtk.PositionType.BOTTOM, "115")
        self.slider.add_mark(300, Gtk.PositionType.BOTTOM, "300")
        self.slider.connect('value-changed', self._on_slider_changed)
        self.append(self.slider)
        
        # Control buttons row
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(8)
        
        # -3ms button
        self.minus_btn = Gtk.Button(label="-3")
        self.minus_btn.set_css_classes(['circular'])
        self.minus_btn.set_tooltip_text("Decrease by 3ms")
        self.minus_btn.connect('clicked', lambda b: self._adjust_delay(-3))
        btn_box.append(self.minus_btn)
        
        # +3ms button
        self.plus_btn = Gtk.Button(label="+3")
        self.plus_btn.set_css_classes(['circular'])
        self.plus_btn.set_tooltip_text("Increase by 3ms")
        self.plus_btn.connect('clicked', lambda b: self._adjust_delay(3))
        btn_box.append(self.plus_btn)
        
        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(False)
        spacer.set_size_request(24, 1)
        btn_box.append(spacer)
        
        # Reset button
        self.reset_btn = Gtk.Button(label="Reset to 115ms")
        self.reset_btn.set_tooltip_text("Reset to default 115ms")
        self.reset_btn.connect('clicked', self._on_reset)
        btn_box.append(self.reset_btn)
        
        self.append(btn_box)
        
        # Status section
        self.append(Gtk.Separator())
        
        status_grid = Gtk.Grid()
        status_grid.set_column_spacing(12)
        status_grid.set_row_spacing(8)
        status_grid.add_css_class('glass-card-inner')
        
        # Bluetooth status
        bt_icon = Gtk.Label(label="📡")
        status_grid.attach(bt_icon, 0, 0, 1, 1)
        
        bt_label = Gtk.Label(label="Bluetooth:")
        bt_label.set_halign(Gtk.Align.START)
        bt_label.set_css_classes(['subtitle-label'])
        status_grid.attach(bt_label, 1, 0, 1, 1)
        
        self.bt_status = Gtk.Label(label="Checking...")
        self.bt_status.set_halign(Gtk.Align.START)
        self.bt_status.set_hexpand(True)
        status_grid.attach(self.bt_status, 2, 0, 1, 1)
        
        # Jack status
        jack_icon = Gtk.Label(label="🎧")
        status_grid.attach(jack_icon, 0, 1, 1, 1)
        
        jack_label = Gtk.Label(label="Jack Output:")
        jack_label.set_halign(Gtk.Align.START)
        jack_label.set_css_classes(['subtitle-label'])
        status_grid.attach(jack_label, 1, 1, 1, 1)
        
        self.jack_status = Gtk.Label(label="Checking...")
        self.jack_status.set_halign(Gtk.Align.START)
        status_grid.attach(self.jack_status, 2, 1, 1, 1)
        
        # Sync status
        sync_icon = Gtk.Label(label="🔗")
        status_grid.attach(sync_icon, 0, 2, 1, 1)
        
        sync_label = Gtk.Label(label="Sync Status:")
        sync_label.set_halign(Gtk.Align.START)
        sync_label.set_css_classes(['subtitle-label'])
        status_grid.attach(sync_label, 1, 2, 1, 1)
        
        self.sync_status = Gtk.Label(label="Checking...")
        self.sync_status.set_halign(Gtk.Align.START)
        status_grid.attach(self.sync_status, 2, 2, 1, 1)
        
        self.append(status_grid)
    
    def _on_slider_changed(self, slider):
        """Handle slider value changes - auto-apply delay."""
        if self._updating:
            return
        value = int(slider.get_value())
        self.delay_label.set_text(str(value))
        # Auto-apply the delay
        self._apply_delay(value)
    
    def _adjust_delay(self, delta: int):
        """Adjust delay by delta ms."""
        current = int(self.slider.get_value())
        new_value = max(0, min(300, current + delta))
        self._updating = True
        self.slider.set_value(new_value)
        self.delay_label.set_text(str(new_value))
        self._updating = False
        self._apply_delay(new_value)
    
    def _on_reset(self, button):
        """Reset to default 115ms."""
        self._updating = True
        self.slider.set_value(115)
        self.delay_label.set_text("115")
        self._updating = False
        self._apply_delay(115)
    
    def _apply_delay(self, value: int):
        """Apply delay to audio backend."""
        audio.set_jack_delay(value)
        self._update_status()
    
    def _start_monitoring(self):
        """Start periodic status updates."""
        self._update_status()
        GLib.timeout_add_seconds(3, self._update_status)
    
    def _update_status(self) -> bool:
        """Update status display."""
        state = audio.get_state()
        
        # Bluetooth
        if state.bt_connected:
            self.bt_status.set_text(f"{config.get('bt_speaker_name')} ● Connected")
            self.bt_status.set_css_classes(['status-connected'])
        else:
            self.bt_status.set_text("Not connected")
            self.bt_status.set_css_classes(['status-disconnected'])
        
        # Jack
        if state.jack_sink_available:
            delay = state.jack_loopback_delay or config.jack_delay
            self.jack_status.set_text(f"Analog Stereo ({delay}ms) ● Active")
            self.jack_status.set_css_classes(['status-active'])
        else:
            self.jack_status.set_text("Not available")
            self.jack_status.set_css_classes(['status-disconnected'])
        
        # Sync
        if state.virtual_sink_exists and state.jack_loopback_exists:
            if state.bt_loopback_exists:
                self.sync_status.set_text("● Running (Jack + BT)")
            else:
                self.sync_status.set_text("● Running (Jack only)")
            self.sync_status.set_css_classes(['status-connected'])
        else:
            self.sync_status.set_text("● Not configured")
            self.sync_status.set_css_classes(['status-disconnected'])
        
        return True  # Continue timeout
