#!/usr/bin/env python3
"""
Audio Sync Master - Professional Audio Control for Linux

A GTK4 application for managing Bluetooth/analog audio synchronization
with an advanced 10-band parametric equalizer.

Features:
- Idempotent audio sync (won't break on restart)
- Adjustable Jack audio delay (default 115ms)
- 10-band parametric EQ with presets
- Dark glassmorphism UI

Requirements:
- Python 3.10+
- GTK4 and libadwaita
- PulseAudio

Install dependencies:
    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils

Run:
    python3 main.py
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

import sys
import os
import signal
from pathlib import Path

# Ensure we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from audio_backend import audio
from config import config
from ui.main_window import MainWindow

# Initialize Adwaita
Adw.init()


class AudioSyncApp(Adw.Application):
    """Main application class."""
    
    def __init__(self):
        super().__init__(
            application_id="com.audiosync.master",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        
        self.window = None
        
        # Handle SIGINT gracefully
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_sigint)
    
    def do_activate(self):
        """Handle application activation."""
        if not self.window:
            self.window = MainWindow(self)
        self.window.present()
    
    def do_startup(self):
        """Handle application startup."""
        Adw.Application.do_startup(self)
        
        # Create app actions
        action = Gio.SimpleAction.new("quit", None)
        action.connect("activate", self._on_quit)
        self.add_action(action)
        
        action = Gio.SimpleAction.new("about", None)
        action.connect("activate", self._on_about)
        self.add_action(action)
        
        # Set keyboard shortcuts
        self.set_accels_for_action("app.quit", ["<Control>q"])
    
    def _on_quit(self, action, param):
        """Handle quit action."""
        self.quit()
    
    def _on_sigint(self):
        """Handle Ctrl+C."""
        self.quit()
        return GLib.SOURCE_REMOVE
    
    def _on_about(self, action, param):
        """Show about dialog."""
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name="Audio Sync Master",
            application_icon="audio-speakers",
            version="1.0.0",
            developer_name="Audio Sync Team",
            copyright="© 2026",
            comments="Professional audio synchronization and equalization for Linux",
            website="https://github.com/audiosync/master",
            license_type=Gtk.License.MIT_X11
        )
        about.present()


def main():
    """Application entry point."""
    # Check for required dependencies
    try:
        import subprocess
        result = subprocess.run(['pactl', '--version'], capture_output=True)
        if result.returncode != 0:
            print("Error: PulseAudio (pactl) not found. Please install pulseaudio-utils.")
            sys.exit(1)
    except FileNotFoundError:
        print("Error: PulseAudio (pactl) not found. Please install pulseaudio-utils.")
        sys.exit(1)
    
    # Run the app
    app = AudioSyncApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
