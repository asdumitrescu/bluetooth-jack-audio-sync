# 🔊 Audio Sync Master

**Sync your Bluetooth speaker with analog jack output - play them as one unified speaker system!**

A GTK4 Linux application for synchronizing JBL PartyBox Encore 2 (or any Bluetooth speaker) with your computer's analog jack output, with adjustable delay compensation and a professional 10-band parametric equalizer.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux-green.svg)
![Python](https://img.shields.io/badge/python-3.10+-yellow.svg)

---

## ✨ Features

- 🎧 **Audio Sync**: Combine Bluetooth and Jack audio outputs into one unified output
- ⏱️ **Delay Compensation**: Adjustable Jack delay (0-300ms, default 115ms) to sync with Bluetooth latency
- 🎛️ **10-Band Parametric EQ**: Professional equalizer with presets (Bass, Rock, Jazz, etc.)
- 🔄 **Idempotent**: Safe to restart - won't break your existing audio setup
- 🎨 **Modern Dark UI**: Beautiful glassmorphism GTK4 interface
- 📦 **Easy Install**: `.deb` package included for one-click installation

---

## 🖥️ Screenshots

The application features a sleek dark interface with two main tabs:

- **Sync Tab**: Delay slider with ±3ms fine adjustment, real-time status display
- **Equalizer Tab**: 10-band vertical sliders with preset buttons

---

## 📦 Installation

### Quick Install (Debian/Ubuntu)

```bash
# Download and install the .deb package
sudo dpkg -i audiosync-master_1.0.0_all.deb

# Install any missing dependencies
sudo apt-get install -f
```

### Dependencies

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pulseaudio-utils swh-plugins
```

---

## 🚀 Usage

### Launch from Terminal

```bash
audiosync-master
```

### Or find "Audio Sync Master" in your application menu

---

## ⚙️ How It Works

1. **Virtual Master Sink**: Creates a PulseAudio null sink (`audio_master`) as the default output
2. **Loopbacks**: Routes audio from the master to both Jack and Bluetooth with configurable delays
3. **Delay Compensation**: Jack output is delayed (default 115ms) to compensate for Bluetooth latency
4. **EQ Processing**: Optional LADSPA-based equalizer applied before output splitting

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Your App   │ ──► │ audio_master │ ──► │  Jack (115ms delay)
└─────────────┘     │  (or eq_sink)│     └─────────────┘
                    │              │     ┌─────────────┐
                    │              │ ──► │  Bluetooth (1ms)
                    └──────────────┘     └─────────────┘
```

---

## 🎛️ Equalizer Presets

| Preset | Description |
|--------|-------------|
| Flat | No EQ (0dB all bands) |
| Bass Boost | Enhanced low frequencies |
| Treble | Enhanced high frequencies |
| Vocal | Clarity for voice/speech |
| Rock | Classic rock curve |
| Electronic | Deep bass + crisp highs |
| Jazz | Warm, balanced sound |
| Classical | Natural acoustic sound |
| Hip Hop | Heavy bass emphasis |
| Loudness | Enhanced both ends |

---

## 📁 Project Structure

```
audiosync-master/
├── main.py              # Application entry point
├── config.py            # Settings persistence (~/.config/audiosync/)
├── audio_backend.py     # PulseAudio control (idempotent)
├── equalizer.py         # LADSPA 10-band EQ
├── ui/
│   ├── main_window.py   # GTK4 main window
│   ├── delay_panel.py   # Delay slider controls
│   ├── equalizer_panel.py # EQ sliders + presets
│   └── style.css        # Dark glassmorphism theme
├── debian/              # Debian package files
└── icons/               # Application icon (SVG)
```

---

## 🔧 Configuration

Settings are saved to `~/.config/audiosync/settings.json`:

```json
{
  "jack_delay_ms": 115,
  "jack_sink": "alsa_output.pci-0000_00_1f.3.analog-stereo",
  "bt_speaker_name": "JBL PartyBox Encore 2",
  "eq_enabled": false,
  "eq_bands": {"31": 0, "63": 0, ...}
}
```

---

## 🛠️ Building from Source

```bash
# Clone the repository
git clone https://github.com/asdumitrescu/bluetooth-jack-audio-sync.git
cd bluetooth-jack-audio-sync

# Build the .deb package
./build-deb.sh

# Install
sudo dpkg -i audiosync-master_1.0.0_all.deb
```

---

## 📋 Requirements

- **OS**: Linux (tested on Ubuntu 22.04+)
- **Python**: 3.10+
- **Audio**: PulseAudio
- **Display**: X11 or Wayland (GTK4)

---

## 🤝 Contributing

Contributions are welcome! Feel free to:

- Report bugs
- Suggest features
- Submit pull requests

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- Built with [GTK4](https://gtk.org/) and [libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/)
- EQ powered by [SWH LADSPA Plugins](http://plugin.org.uk/)
- Inspired by the need to sync JBL PartyBox Encore 2 with desktop speakers

---

**Made with ❤️ for the Linux audio community**
