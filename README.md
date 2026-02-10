# Audio Sync Master

**Universal Audio Synchronization for Linux**

Audio Sync Master automatically detects and synchronizes **ALL** audio output devices on your Linux system — analog audio cards, USB DACs, Bluetooth speakers — simultaneously with per-device delay control.

## Disclaimer: Experimental Software

> **This software is provided "AS IS" and has NOT been extensively tested on all hardware configurations.**
>
> It is an open-source project intended for users who want to play audio through multiple outputs at the same time on Linux. While designed to be safe (idempotent), experimental audio routing can sometimes require restarting PulseAudio or your computer if things get stuck.
>
> **Contributors are welcome!** If you encounter issues, please fork the repository, modify the code to fit your needs, and submit a Pull Request.

## Key Features

- **Auto-Detect Everything**: Automatically finds ALL analog audio cards and Bluetooth speakers connected to your system. No manual configuration needed.
- **Real-Time Monitoring**: Plug-and-play support. Connect a new Bluetooth speaker or plug in a USB audio device — it gets detected and synced automatically.
- **Per-Device Delay Control**: Fine-tune the latency for each individual device to achieve perfect synchronization across all outputs.
- **10-Band Equalizer**: Built-in parametric equalizer with presets (Bass Boost, Rock, Electronic, Jazz, Classical, Hip Hop, Loudness, and more).
- **Idempotent Design**: Safe to restart at any time. The backend only creates loopbacks if they don't already exist, preventing duplicate streams and echoes.
- **PulseAudio & PipeWire**: Works with both PulseAudio and PipeWire (via pactl compatibility).
- **Modern UI**: Sleek, dark glassmorphism-inspired GTK4/libadwaita interface.

## How It Works

```
[Your Music App] --> [Audio Master (virtual sink)]
                           |
                           |--> loopback --> Analog Card 1  (delay: 121ms)
                           |--> loopback --> Analog Card 2  (delay: 115ms)
                           |--> loopback --> BT Speaker 1   (delay: 1ms)
                           |--> loopback --> BT Speaker 2   (delay: 1ms)
```

Audio Sync Master creates a virtual PulseAudio sink called `audio_master` and routes all application audio through it. It then creates individual loopback modules to each detected output device, each with its own configurable delay.

## Installation

### Option 1: Install from .deb (Recommended)

```bash
# Download the .deb file from releases
sudo dpkg -i audiosync-master_2.0.0_all.deb
sudo apt-get install -f  # Install any missing dependencies
```

### Option 2: Run from source

```bash
# Install dependencies
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils

# Optional: LADSPA EQ plugin
sudo apt install swh-plugins

# Clone and run
git clone https://github.com/asdumitrescu/audiosync-master.git
cd audiosync-master
python3 main.py
```

### Build .deb from source

```bash
git clone https://github.com/asdumitrescu/audiosync-master.git
cd audiosync-master
chmod +x build-deb.sh
./build-deb.sh
sudo dpkg -i audiosync-master_2.0.0_all.deb
```

## Usage

1. **Launch**: Open Audio Sync Master from your application menu, or run `audiosync-master` in terminal.
2. **Devices Tab**: Shows all detected audio devices with their sync status. Each device has its own delay slider.
3. **Adjust Delay**: Bluetooth speakers typically have 50-150ms of internal latency. Increase the delay on your analog outputs until they match the Bluetooth audio.
4. **Equalizer Tab**: Apply sound profiles or create your own custom EQ.
5. **Resync**: Click "Resync All" if things get out of whack. Click "Cleanup" to remove all loopbacks.

## Requirements

- Python 3.10+
- GTK4 and libadwaita
- PulseAudio or PipeWire (with `pactl`)
- Linux (Ubuntu 22.04+, Fedora 36+, Arch, etc.)

## Project Structure

```
audiosync-master/
  main.py              # Application entry point
  audio_backend.py     # Multi-device PulseAudio/PipeWire control
  config.py            # Per-device configuration persistence
  equalizer.py         # 10-band LADSPA parametric EQ
  ui/
    main_window.py     # GTK4 main window with tabs
    delay_panel.py     # Device list with per-device delay controls
    equalizer_panel.py # EQ sliders and presets
    style.css          # Dark glassmorphism theme
  debian/              # .deb packaging structure
  build-deb.sh         # Build script for .deb package
```

## Configuration

Settings are stored in `~/.config/audiosync/settings.json`:

- Per-device delay overrides
- Default analog delay (121ms)
- Default Bluetooth delay (1ms)
- EQ bands and preset
- EQ enabled state

## Troubleshooting

**No devices detected**: Make sure PulseAudio or PipeWire is running. Check with `pactl list sinks short`.

**Bluetooth speaker not appearing**: Ensure it's connected via your system's Bluetooth settings first. The app detects `bluez_sink.*` / `bluez_output.*` entries.

**Audio sounds doubled/echoed**: Click "Cleanup" to remove all loopbacks, then "Resync All" to rebuild cleanly.

**Scratchy/crackling analog audio (PipeWire)**: The app uses PipeWire-safe loopback flags automatically. If audio still crackles, click "Reset Audio" to clear corrupted WirePlumber state and restart PipeWire cleanly.

**No sound after cleanup on PipeWire**: PipeWire's WirePlumber may cache old module state. Click "Reset Audio" to do a full factory reset, then "Resync All".

**App won't start**: Install dependencies: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils`

## License

This project is licensed under the **MIT License**. You are free to use, modify, distribute, and sell this software. See the [LICENSE](LICENSE) file for details.
