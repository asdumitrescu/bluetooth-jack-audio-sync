#!/usr/bin/env python3
"""
Device Panel - Shows all detected audio devices with sync status.
PipeWire mode: combine-sink handles sync natively (no per-device delay).
PulseAudio mode: loopback with per-device delay slider for analog.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from audio_backend import audio, OutputDevice
from config import config, DEFAULT_ANALOG_DELAY

BASE_DELAY = DEFAULT_ANALOG_DELAY  # 121ms
MIN_OFFSET = -150
MAX_OFFSET = 150


class DeviceRow(Gtk.Box):
    """A single device row with status and optional delay slider (PulseAudio only)."""

    def __init__(self, device: OutputDevice, on_offset_changed, is_pipewire: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class('device-row')

        self.device = device
        self._on_offset_changed = on_offset_changed
        self._is_pipewire = is_pipewire
        self._updating = False

        self._setup_ui()

    def _format_offset(self, offset: int) -> str:
        if offset > 0:
            return f"+{offset}ms"
        elif offset == 0:
            return "0ms"
        return f"{offset}ms"

    def _setup_ui(self):
        # Header row: icon + name + type badge + status
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        # Device icon
        if self.device.device_type == "bluetooth":
            icon_text = "\U0001F4F6"
        else:
            icon_text = "\U0001F3A7"
        icon = Gtk.Label(label=icon_text)
        icon.set_css_classes(['device-icon'])
        header.append(icon)

        # Device name
        name_label = Gtk.Label(label=self.device.display_name)
        name_label.set_css_classes(['device-name'])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        name_label.set_max_width_chars(30)
        header.append(name_label)

        # Type badge
        type_text = "BT" if self.device.device_type == "bluetooth" else "Analog"
        type_badge = Gtk.Label(label=type_text)
        badge_class = 'badge-bt' if self.device.device_type == "bluetooth" else 'badge-analog'
        type_badge.set_css_classes(['device-badge', badge_class])
        header.append(type_badge)

        # Sync status indicator
        self.status_dot = Gtk.Label(label="")
        self.status_dot.set_css_classes(['status-dot'])
        header.append(self.status_dot)

        self.append(header)

        # Delay offset slider — only for analog devices on PulseAudio
        if self.device.device_type == "analog" and not self._is_pipewire:
            delay_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            delay_row.set_margin_start(32)

            delay_label = Gtk.Label(label="Offset:")
            delay_label.set_css_classes(['subtitle-label'])
            delay_row.append(delay_label)

            current_offset = config.get_device_offset(self.device.sink_name)

            self.offset_value = Gtk.Label(label=self._format_offset(current_offset))
            self.offset_value.set_css_classes(['delay-value'])
            self.offset_value.set_size_request(70, -1)
            delay_row.append(self.offset_value)

            self.slider = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, MIN_OFFSET, MAX_OFFSET, 1
            )
            self.slider.set_value(current_offset)
            self.slider.set_draw_value(False)
            self.slider.set_hexpand(True)
            self.slider.add_mark(MIN_OFFSET, Gtk.PositionType.BOTTOM, str(MIN_OFFSET))
            self.slider.add_mark(0, Gtk.PositionType.BOTTOM, "0")
            self.slider.add_mark(MAX_OFFSET, Gtk.PositionType.BOTTOM, f"+{MAX_OFFSET}")
            self.slider.connect('value-changed', self._on_slider_changed)
            delay_row.append(self.slider)

            minus_btn = Gtk.Button(label="-3")
            minus_btn.set_css_classes(['circular', 'mini-btn'])
            minus_btn.set_tooltip_text("Decrease offset by 3ms")
            minus_btn.connect('clicked', lambda b: self._adjust_offset(-3))
            delay_row.append(minus_btn)

            plus_btn = Gtk.Button(label="+3")
            plus_btn.set_css_classes(['circular', 'mini-btn'])
            plus_btn.set_tooltip_text("Increase offset by 3ms")
            plus_btn.connect('clicked', lambda b: self._adjust_offset(3))
            delay_row.append(plus_btn)

            self.append(delay_row)

    def _on_slider_changed(self, slider):
        if self._updating:
            return
        offset = int(slider.get_value())
        self.offset_value.set_text(self._format_offset(offset))
        self._on_offset_changed(self.device.sink_name, offset)

    def _adjust_offset(self, delta: int):
        current = int(self.slider.get_value())
        new_val = max(MIN_OFFSET, min(MAX_OFFSET, current + delta))
        self._updating = True
        self.slider.set_value(new_val)
        self.offset_value.set_text(self._format_offset(new_val))
        self._updating = False
        self._on_offset_changed(self.device.sink_name, new_val)

    def update_status(self, is_synced: bool):
        """Update the visual sync status."""
        if is_synced:
            self.status_dot.set_text("\u2B24")  # Filled circle
            self.status_dot.set_css_classes(['status-dot', 'status-connected'])
        else:
            self.status_dot.set_text("\u25CB")  # Empty circle
            self.status_dot.set_css_classes(['status-dot', 'status-disconnected'])


class DelayPanel(Gtk.Box):
    """Panel showing all detected devices with sync controls."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class('glass-card')

        self._device_rows: dict[str, DeviceRow] = {}
        self._setup_ui()
        self._start_monitoring()

    def _setup_ui(self):
        # Title
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box.set_halign(Gtk.Align.START)

        icon = Gtk.Label(label="\U0001F50A")  # Speaker icon
        icon.set_css_classes(['title-label'])
        title_box.append(icon)

        title = Gtk.Label(label="Audio Devices & Sync")
        title.set_css_classes(['title-label'])
        title_box.append(title)

        self.append(title_box)

        # Summary status
        self.summary_label = Gtk.Label(label="Scanning for devices...")
        self.summary_label.set_css_classes(['subtitle-label'])
        self.summary_label.set_halign(Gtk.Align.START)
        self.summary_label.set_margin_start(4)
        self.append(self.summary_label)

        self.append(Gtk.Separator())

        # Scrollable device list
        self.device_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.device_list.add_css_class('device-list')
        self.append(self.device_list)

        # Empty state label (shown when no devices)
        self.empty_label = Gtk.Label(
            label="No audio output devices detected.\n"
                  "Connect a Bluetooth speaker or plug in an analog audio device."
        )
        self.empty_label.set_css_classes(['subtitle-label'])
        self.empty_label.set_justify(Gtk.Justification.CENTER)
        self.empty_label.set_margin_top(24)
        self.empty_label.set_margin_bottom(24)
        self.device_list.append(self.empty_label)

        # Control buttons
        self.append(Gtk.Separator())

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(8)

        resync_btn = Gtk.Button(label="Resync All")
        resync_btn.set_css_classes(['suggested-action'])
        resync_btn.set_tooltip_text("Rebuild audio sync")
        resync_btn.connect('clicked', self._on_resync)
        btn_box.append(resync_btn)

        cleanup_btn = Gtk.Button(label="Cleanup")
        cleanup_btn.set_tooltip_text("Remove all sync modules")
        cleanup_btn.connect('clicked', self._on_cleanup)
        btn_box.append(cleanup_btn)

        reset_btn = Gtk.Button(label="Reset Audio")
        reset_btn.set_css_classes(['destructive-action'])
        reset_btn.set_tooltip_text("Factory reset: clear all audio state and restart PipeWire")
        reset_btn.connect('clicked', self._on_factory_reset)
        btn_box.append(reset_btn)

        self.append(btn_box)

    def _on_offset_changed(self, sink_name: str, offset_ms: int):
        """Handle offset change for an analog device (PulseAudio only)."""
        audio.set_analog_offset(sink_name, offset_ms)

    def _on_resync(self, button):
        """Force resync all devices."""
        self.summary_label.set_text("Resyncing...")
        success, message = audio.setup_sync()
        self._refresh_device_list()
        self.summary_label.set_text(f"\u2713 {message}" if success else f"\u26A0 {message}")

    def _on_cleanup(self, button):
        """Remove all sync modules."""
        audio.cleanup()
        self._refresh_device_list()
        self.summary_label.set_text("Sync removed. Click 'Resync All' to restore.")

    def _on_factory_reset(self, button):
        """Factory reset audio system."""
        self.summary_label.set_text("Resetting audio system...")
        success, message = audio.factory_reset()
        self._refresh_device_list()
        self.summary_label.set_text(f"\u2713 {message}" if success else f"\u26A0 {message}")

    def _start_monitoring(self):
        """Start periodic status updates and device monitoring."""
        GLib.idle_add(self._refresh_device_list)
        GLib.timeout_add_seconds(5, self._periodic_refresh)
        audio.start_monitor(on_change=lambda: GLib.idle_add(self._refresh_device_list))

    def _periodic_refresh(self) -> bool:
        """Periodic refresh with auto-repair."""
        self._refresh_device_list()
        return True

    def _refresh_device_list(self) -> bool:
        """Refresh the device list from current audio state."""
        state = audio.get_state()

        # Auto-repair: if combine sink is missing or devices are unsynced, fix it
        needs_repair = False
        if not state.combine_sink_exists:
            needs_repair = True
        elif state.unsynced_devices:
            needs_repair = True

        if needs_repair:
            audio.setup_sync()
            state = audio.get_state()

        # Rebuild device rows if the device list changed
        current_sinks = {d.sink_name for d in state.devices}
        displayed_sinks = set(self._device_rows.keys())

        if current_sinks != displayed_sinks:
            self._rebuild_device_rows(state)
        else:
            for device in state.devices:
                row = self._device_rows.get(device.sink_name)
                if row:
                    row.update_status(device.is_synced)

        # Update summary
        analog_count = len(state.analog_devices)
        bt_count = len(state.bluetooth_devices)
        synced_count = len(state.synced_devices)
        total = len(state.devices)

        if total == 0:
            self.summary_label.set_text("No output devices detected")
        else:
            parts = []
            if analog_count:
                parts.append(f"{analog_count} analog")
            if bt_count:
                parts.append(f"{bt_count} bluetooth")
            devices_text = ", ".join(parts)
            self.summary_label.set_text(
                f"{synced_count}/{total} synced ({devices_text})"
            )

        return False

    def _rebuild_device_rows(self, state):
        """Rebuild the device rows from scratch."""
        child = self.device_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.device_list.remove(child)
            child = next_child
        self._device_rows.clear()

        if not state.devices:
            self.device_list.append(self.empty_label)
            return

        sorted_devices = sorted(state.devices, key=lambda d: (0 if d.device_type == "analog" else 1, d.display_name))

        for device in sorted_devices:
            row = DeviceRow(device, self._on_offset_changed, state.is_pipewire)
            row.update_status(device.is_synced)
            self._device_rows[device.sink_name] = row
            self.device_list.append(row)
