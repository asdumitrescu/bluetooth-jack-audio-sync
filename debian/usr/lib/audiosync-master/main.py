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
- PipeWire and PulseAudio compatible
- Auto-detects sinks and BT codec

Requirements:
- Python 3.10+
- GTK4 and libadwaita
- PulseAudio or PipeWire (with pipewire-pulse)

Install dependencies:
    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils swh-plugins

Run:
    python3 main.py
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio

import sys
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
        if not self.window:
            self.window = MainWindow(self)
        self.window.present()

    def do_startup(self):
        Adw.Application.do_startup(self)

        action = Gio.SimpleAction.new("quit", None)
        action.connect("activate", self._on_quit)
        self.add_action(action)

        action = Gio.SimpleAction.new("about", None)
        action.connect("activate", self._on_about)
        self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_shutdown(self):
        """Clean up audio modules on exit."""
        audio.cleanup()
        Adw.Application.do_shutdown(self)

    def _on_quit(self, action, param):
        self.quit()

    def _on_sigint(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def _on_about(self, action, param):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name="Audio Sync Master",
            application_icon="audio-speakers",
            version="1.1.0",
            developer_name="Andrei Dumitrescu",
            copyright="\u00a9 2026",
            comments="Professional audio synchronization and equalization for Linux.\nSupports PulseAudio and PipeWire.",
            website="https://github.com/asdumitrescu/bluetooth-jack-audio-sync",
            license_type=Gtk.License.MIT_X11
        )
        about.present()


def main():
    """Application entry point."""
    # Check for pactl (works with both PulseAudio and PipeWire)
    import subprocess
    try:
        result = subprocess.run(['pactl', '--version'], capture_output=True, timeout=5)
        if result.returncode != 0:
            print("Error: pactl not found. Install pulseaudio-utils or pipewire-pulse.")
            sys.exit(1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("Error: pactl not found. Install pulseaudio-utils or pipewire-pulse.")
        sys.exit(1)

    app = AudioSyncApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
