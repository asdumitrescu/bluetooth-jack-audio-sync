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
        self.set_default_size(750, 650)
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
        icon = Gtk.Label(label="\U0001F3B5")
        icon.set_css_classes(['title-label'])
        title_box.append(icon)

        title = Gtk.Label(label="Audio Sync Master")
        title.set_css_classes(['title-label'])
        title_box.append(title)

        header.set_title_widget(title_box)

        # About button
        about_btn = Gtk.Button(icon_name="help-about-symbolic")
        about_btn.set_tooltip_text("About")
        about_btn.connect('clicked', lambda b: self.get_application().activate_action('about', None))
        header.pack_end(about_btn)

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
        self.stack.add_titled(self.delay_panel, "devices", "\U0001F50A Devices")
        self.stack.add_titled(self.equalizer_panel, "equalizer", "\U0001F39B Equalizer")

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

    def _do_initial_setup(self):
        """Run initial setup in main thread."""
        self.status_label.set_text("Detecting audio devices...")

        success, message = audio.setup_sync()

        if success:
            self.status_label.set_text(f"\u2713 {message}")
        else:
            self.status_label.set_text(f"\u26A0 {message}")

        return False

    def _on_refresh(self, button):
        """Handle refresh button click."""
        self.status_label.set_text("Refreshing...")
        success, message = audio.setup_sync()
        self.status_label.set_text(f"\u2713 {message}" if success else f"\u26A0 {message}")
