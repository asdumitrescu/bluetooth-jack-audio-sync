#!/usr/bin/env python3
"""
Audio Sync Master - Professional Audio Control for Linux

A GTK4 application that automatically detects and synchronizes ALL
audio output devices (analog cards + Bluetooth speakers) with
per-device delay control and a 10-band parametric equalizer.

Features:
- Auto-detects ALL analog audio cards and Bluetooth speakers
- Real-time device monitoring (plug-and-play)
- Per-device adjustable delay for perfect sync
- 10-band parametric EQ with presets
- Dark glassmorphism UI
- Works with PulseAudio and PipeWire

Requirements:
- Python 3.10+
- GTK4 and libadwaita
- PulseAudio or PipeWire (with pactl)

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
import subprocess
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

    def do_shutdown(self):
        """Handle application shutdown."""
        audio.stop_monitor()
        Adw.Application.do_shutdown(self)

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
            version="2.0.0",
            developer_name="Andrei Dumitrescu",
            copyright="\u00a9 2026",
            comments=(
                "Automatically detect and sync ALL audio outputs.\n"
                "Analog cards + Bluetooth speakers with per-device delay."
            ),
            license_type=Gtk.License.MIT_X11
        )
        about.present()


def _check_audio_server() -> bool:
    """Check for PulseAudio or PipeWire with pactl support."""
    try:
        result = subprocess.run(['pactl', 'info'], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            if 'PipeWire' in result.stdout:
                print("Audio server: PipeWire")
            else:
                print("Audio server: PulseAudio")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def main():
    """Application entry point."""
    if not _check_audio_server():
        print("Error: No compatible audio server found.")
        print("Please install PulseAudio or PipeWire:")
        print("  sudo apt install pulseaudio-utils")
        print("  # or for PipeWire:")
        print("  sudo apt install pipewire pipewire-pulse")
        sys.exit(1)

    app = AudioSyncApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
