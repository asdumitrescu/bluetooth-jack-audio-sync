#!/usr/bin/env python3
"""
Main Window - Primary application window with tabbed interface.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Gio, GLib, Adw

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from audio_backend import audio
from config import config
from ui.delay_panel import DelayPanel
from ui.equalizer_panel import EqualizerPanel


class MainWindow(Adw.ApplicationWindow):
    """Main application window."""
    
    def __init__(self, application):
        super().__init__(application=application)
        
        self.set_title("Audio Sync Master")
        self.set_default_size(700, 600)
        self.set_size_request(600, 500)
        
        self._load_css()
        self._setup_ui()
        self._initial_setup()
    
    def _load_css(self):
        """Load custom CSS theme."""
        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).parent / "style.css"
        
        if css_path.exists():
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
    
    def _setup_ui(self):
        """Build the main UI."""
        
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        
        # Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Label(label="🎵")
        icon.set_css_classes(['title-label'])
        title_box.append(icon)
        
        title = Gtk.Label(label="Audio Sync Master")
        title.set_css_classes(['title-label'])
        title_box.append(title)
        
        header.set_title_widget(title_box)
        
        # Settings button
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect('clicked', self._on_settings)
        header.pack_end(settings_btn)
        
        # Refresh button
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh audio setup")
        refresh_btn.connect('clicked', self._on_refresh)
        header.pack_end(refresh_btn)
        
        main_box.append(header)
        
        # Tab switcher
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(12)
        switcher.set_margin_bottom(4)
        main_box.append(switcher)
        
        # Scrolled content area
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.stack)
        main_box.append(scroll)
        
        # Create panels
        self.delay_panel = DelayPanel()
        self.delay_panel.set_margin_start(16)
        self.delay_panel.set_margin_end(16)
        self.delay_panel.set_margin_bottom(16)
        
        self.equalizer_panel = EqualizerPanel()
        self.equalizer_panel.set_margin_start(16)
        self.equalizer_panel.set_margin_end(16)
        self.equalizer_panel.set_margin_bottom(16)
        
        # Add to stack
        self.stack.add_titled(self.delay_panel, "sync", "🔊 Sync")
        self.stack.add_titled(self.equalizer_panel, "equalizer", "🎛️ Equalizer")
        
        # Status bar
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_bar.set_margin_start(16)
        status_bar.set_margin_end(16)
        status_bar.set_margin_top(8)
        status_bar.set_margin_bottom(12)
        
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_css_classes(['status-label'])
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_hexpand(True)
        status_bar.append(self.status_label)
        
        main_box.append(status_bar)
        
        self.set_content(main_box)
    
    def _initial_setup(self):
        """Perform initial audio setup (idempotent)."""
        GLib.idle_add(self._do_initial_setup)
        # Start monitoring for changes (e.g. new devices)
        GLib.timeout_add_seconds(5, self._check_status)
    
    def _check_status(self):
        """Periodically check if sync is needed."""
        if not audio.is_setup_complete():
            print("Detected change in audio devices, resyncing...")
            self.status_label.set_text("New device detected, syncing...")
            success, message = audio.setup_sync()
            if success:
                self.status_label.set_text(f"✓ {message}")
            else:
                self.status_label.set_text(f"⚠ {message}")
        return True

    def _do_initial_setup(self):
        """Run initial setup in main thread."""
        self.status_label.set_text("Checking audio configuration...")
        
        success, message = audio.setup_sync()
        
        if success:
            if "already configured" in message.lower():
                self.status_label.set_text("✓ Audio sync active")
            else:
                self.status_label.set_text(f"✓ {message}")
        else:
            self.status_label.set_text(f"⚠ {message}")
        
        return False
    
    def _on_refresh(self, button):
        """Handle refresh button click."""
        self.status_label.set_text("Refreshing...")
        success, message = audio.setup_sync()
        self.status_label.set_text(f"✓ {message}" if success else f"⚠ {message}")
    
    def _on_settings(self, button):
        """Show settings dialog."""
        dialog = SettingsDialog(self)
        dialog.present()


class SettingsDialog(Adw.Window):
    """Settings dialog."""
    
    def __init__(self, parent):
        super().__init__()
        
        self.set_title("Settings")
        self.set_default_size(400, 300)
        self.set_modal(True)
        self.set_transient_for(parent)
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Build settings UI."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        
        # Header
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        main_box.append(header)
        
        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.add_css_class('glass-card')
        
        # Jack sink setting
        jack_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        jack_label = Gtk.Label(label="Jack Sink Name")
        jack_label.set_css_classes(['subtitle-label'])
        jack_label.set_halign(Gtk.Align.START)
        jack_row.append(jack_label)
        
        self.jack_entry = Gtk.Entry()
        self.jack_entry.set_text(config.get('jack_sink'))
        jack_row.append(self.jack_entry)
        content.append(jack_row)
        
        # BT speaker name setting
        bt_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bt_label = Gtk.Label(label="Bluetooth Speaker Name")
        bt_label.set_css_classes(['subtitle-label'])
        bt_label.set_halign(Gtk.Align.START)
        bt_row.append(bt_label)
        
        self.bt_entry = Gtk.Entry()
        self.bt_entry.set_text(config.get('bt_speaker_name'))
        bt_row.append(self.bt_entry)
        content.append(bt_row)
        
        # Minimize to tray
        tray_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        tray_label = Gtk.Label(label="Minimize to system tray")
        tray_label.set_hexpand(True)
        tray_label.set_halign(Gtk.Align.START)
        tray_row.append(tray_label)
        
        self.tray_switch = Gtk.Switch()
        self.tray_switch.set_active(config.get('minimize_to_tray', True))
        tray_row.append(self.tray_switch)
        content.append(tray_row)
        
        # Save button
        save_btn = Gtk.Button(label="Save")
        save_btn.set_css_classes(['suggested-action'])
        save_btn.set_halign(Gtk.Align.END)
        save_btn.set_margin_top(12)
        save_btn.connect('clicked', self._on_save)
        content.append(save_btn)
        
        main_box.append(content)
        self.set_content(main_box)
    
    def _on_save(self, button):
        """Save settings."""
        config.set('jack_sink', self.jack_entry.get_text(), save=False)
        config.set('bt_speaker_name', self.bt_entry.get_text(), save=False)
        config.set('minimize_to_tray', self.tray_switch.get_active(), save=False)
        config.save()
        self.close()
