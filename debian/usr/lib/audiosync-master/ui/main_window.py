#!/usr/bin/env python3
"""
Main Window - Primary application window with tabbed interface.
Settings dialog includes sink auto-discovery.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Gio, GLib, Adw

import threading
import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from audio_backend import audio
from config import config
from pulse_utils import list_sinks, validate_sink_name, is_pipewire
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
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

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

        # Audio server info bar
        server_info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        server_info.set_margin_start(16)
        server_info.set_margin_end(16)
        server_info.set_margin_top(4)

        self.server_label = Gtk.Label(label="")
        self.server_label.set_css_classes(['status-label'])
        self.server_label.set_halign(Gtk.Align.START)
        server_info.append(self.server_label)
        main_box.append(server_info)

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
        """Perform initial audio setup off the main thread."""
        # Show server type
        if is_pipewire():
            self.server_label.set_text("Audio Server: PipeWire (PulseAudio compat)")
        else:
            self.server_label.set_text("Audio Server: PulseAudio")

        # Check if first run — open settings if sinks not configured
        if config.is_first_run or not config.get('jack_sink'):
            self.status_label.set_text("First run — detecting audio devices...")
            GLib.idle_add(self._show_first_run)
        else:
            self.status_label.set_text("Checking audio configuration...")
            self._run_setup_async()

    def _show_first_run(self):
        """Show settings dialog on first run for sink configuration."""
        config.mark_first_run_done()
        dialog = SettingsDialog(self, first_run=True)
        dialog.connect('close-request', lambda d: self._run_setup_async() or False)
        dialog.present()
        return False

    def _run_setup_async(self):
        """Run audio setup off the main thread."""
        def _worker():
            success, message = audio.setup_sync()
            GLib.idle_add(self._finish_setup, success, message)
        threading.Thread(target=_worker, daemon=True).start()

    def _finish_setup(self, success, message):
        if success:
            if "already configured" in message.lower():
                self.status_label.set_text("✓ Audio sync active")
            else:
                self.status_label.set_text(f"✓ {message}")
        else:
            self.status_label.set_text(f"⚠ {message}")
        return False

    def _on_refresh(self, button):
        self.status_label.set_text("Refreshing...")
        self._run_setup_async()

    def _on_settings(self, button):
        dialog = SettingsDialog(self)
        dialog.present()


class SettingsDialog(Adw.Window):
    """Settings dialog with sink auto-discovery."""

    def __init__(self, parent, first_run=False):
        super().__init__()

        self.set_title("Settings" if not first_run else "Welcome — Configure Audio")
        self.set_default_size(500, 420)
        self.set_modal(True)
        self.set_transient_for(parent)
        self._first_run = first_run
        self._sinks = []

        self._setup_ui()
        # Detect sinks off-thread
        threading.Thread(target=self._detect_sinks, daemon=True).start()

    def _setup_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        main_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.add_css_class('glass-card')

        if self._first_run:
            welcome = Gtk.Label(label="Configure your audio outputs below.\nSinks are auto-detected from your system.")
            welcome.set_css_classes(['subtitle-label'])
            welcome.set_halign(Gtk.Align.START)
            welcome.set_wrap(True)
            content.append(welcome)

        # Jack sink setting with dropdown
        jack_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        jack_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        jack_label = Gtk.Label(label="Analog Output Sink")
        jack_label.set_css_classes(['subtitle-label'])
        jack_label.set_halign(Gtk.Align.START)
        jack_label.set_hexpand(True)
        jack_header.append(jack_label)

        detect_btn = Gtk.Button(label="Detect")
        detect_btn.set_tooltip_text("Re-detect available sinks")
        detect_btn.connect('clicked', lambda b: threading.Thread(target=self._detect_sinks, daemon=True).start())
        jack_header.append(detect_btn)
        jack_row.append(jack_header)

        self.jack_combo = Gtk.DropDown()
        self.jack_combo.set_model(Gtk.StringList.new([]))
        jack_row.append(self.jack_combo)

        # Manual entry fallback
        self.jack_entry = Gtk.Entry()
        self.jack_entry.set_text(config.get('jack_sink', ''))
        self.jack_entry.set_placeholder_text("Or type sink name manually...")
        jack_row.append(self.jack_entry)
        content.append(jack_row)

        # BT speaker name setting
        bt_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bt_label = Gtk.Label(label="Bluetooth Speaker Name")
        bt_label.set_css_classes(['subtitle-label'])
        bt_label.set_halign(Gtk.Align.START)
        bt_row.append(bt_label)

        self.bt_entry = Gtk.Entry()
        self.bt_entry.set_text(config.get('bt_speaker_name', ''))
        self.bt_entry.set_placeholder_text("e.g. JBL PartyBox Encore 2")
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
        save_btn = Gtk.Button(label="Save & Apply" if self._first_run else "Save")
        save_btn.set_css_classes(['suggested-action'])
        save_btn.set_halign(Gtk.Align.END)
        save_btn.set_margin_top(12)
        save_btn.connect('clicked', self._on_save)
        content.append(save_btn)

        # Status
        self.dialog_status = Gtk.Label(label="")
        self.dialog_status.set_css_classes(['status-label'])
        content.append(self.dialog_status)

        main_box.append(content)
        self.set_content(main_box)

    def _detect_sinks(self):
        """Detect available sinks off-thread."""
        self._sinks = list_sinks()
        GLib.idle_add(self._populate_sink_dropdown)

    def _populate_sink_dropdown(self):
        """Populate the dropdown with detected sinks."""
        analog_sinks = [s for s in self._sinks if s['is_analog'] and not s['is_bluetooth']]
        if not analog_sinks:
            analog_sinks = [s for s in self._sinks if not s['is_bluetooth'] and 'audio_master' not in s['name']]

        names = [f"{s['name']} ({s['description']})" if s['description'] else s['name'] for s in analog_sinks]
        self.jack_combo.set_model(Gtk.StringList.new(names))

        # Pre-select current config value
        current = config.get('jack_sink', '')
        for i, s in enumerate(analog_sinks):
            if s['name'] == current:
                self.jack_combo.set_selected(i)
                break

        self._analog_sinks = analog_sinks

        if self._first_run and not config.get('bt_speaker_name'):
            # Auto-detect BT speaker name from connected BT sinks
            for s in self._sinks:
                if s['is_bluetooth'] and s['description']:
                    self.bt_entry.set_text(s['description'])
                    break

        return False

    def _on_save(self, button):
        """Save settings with validation."""
        # Get jack sink from dropdown or manual entry
        jack_sink = ''
        if hasattr(self, '_analog_sinks') and self._analog_sinks:
            idx = self.jack_combo.get_selected()
            if idx < len(self._analog_sinks):
                jack_sink = self._analog_sinks[idx]['name']

        # Fall back to manual entry
        manual = self.jack_entry.get_text().strip()
        if manual:
            jack_sink = manual

        # Validate sink name
        if jack_sink and not validate_sink_name(jack_sink):
            self.dialog_status.set_text("Invalid sink name (only a-z, 0-9, _ . : - allowed)")
            return

        bt_name = self.bt_entry.get_text().strip()

        config.set('jack_sink', jack_sink, save=False)
        config.set('bt_speaker_name', bt_name, save=False)
        config.set('minimize_to_tray', self.tray_switch.get_active(), save=False)
        config.save()

        # Reload backend config
        audio._reload_config()

        self.close()
