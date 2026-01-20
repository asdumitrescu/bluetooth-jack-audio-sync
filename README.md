# Audio Sync Master 🎧

**Professional Audio Synchronization & Equalization for Linux**

Audio Sync Master is a powerful GTK4 application designed to solve the common problem of playing audio simultaneously through Bluetooth speakers and analog jack outputs (headphones/speakers) without synchronization issues.

## ⚠️ Disclaimer: Experimental Software

> [!IMPORTANT]  
> **This software is provided "AS IS" and has NOT been extensively tested on all hardware configurations.**
> 
> It is an open-source project intended for users who are comfortable attempting to fix audio routing issues on their Linux systems. While designed to be safe (idempotent), experimental audio routing can sometimes require restarting PulseAudio or your computer if things get stuck.
>
> **Contributors are welcome!** If you encounter issues, please fork the repository, modify the code to fit your needs, and submit a Pull Request.

## Key Features

- **Universal Multi-Device Sync**: Automatically detects and synchronizes **ALL** connected Bluetooth speakers and Analog outputs simultaneously.
- **Auto-Detection**: No manual configuration needed. The app scans for devices on startup and monitors for new connections in real-time (plug-and-play).
- **Adjustable Delay**: Fine-tune the latency for your analog output to perfectly match your Bluetooth speakers' internal processing delay.
- **10-Band Equalizer**: Built-in parametric equalizer with presets (Bass Boost, Rock, Electronic, etc.) to enhance your audio experience.
- **Idempotent Design**: The backend is smart—it only creates loopbacks if they don't exist, preventing duplicate streams and weird echoes.
- **Modern UI**: Sleek, glassmorphism-inspired GTK4 interface.

## Installation

### Prerequisites
- Python 3.10+
- PulseAudio (pactl)
- GTK4 & LibAdwaita

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils
```

### Running from Source
Clone the repository and run the main script:

```bash
git clone https://github.com/asdumitrescu/bluetooth-jack-audio-sync.git
cd audiosync-master
python3 debian/usr/lib/audiosync-master/main.py
```

## Usage

1.  **Launch the App**: Open Audio Sync Master.
2.  **Connect Devices**: Connect your Bluetooth speaker(s) or plug in your analog jack.
3.  **Auto-Sync**: The app will display "New device detected, syncing..." and automatically route audio to the new device.
4.  **Adjust Delay**: 
    - Use the **Sync** tab to adjust the analog delay.
    - Most Bluetooth speakers have a latency of 50-150ms. Use the slider to delay the analog output until it matches the Bluetooth audio perfectly.
5.  **Equalizer**: Switch to the **Equalizer** tab to apply sound profiles.

## Project Structure

- `audio_backend.py`: Core logic for PulseAudio interaction and device detection.
- `main.py`: Application entry point.
- `config.py`: Configuration management (stored in `~/.config/audiosync/`).
- `ui/`: GTK4 user interface components.

## License

This project is licensed under the **MIT License**. You are free to use, modify, distribute, and sell this software. See the [LICENSE](LICENSE) file for details.
