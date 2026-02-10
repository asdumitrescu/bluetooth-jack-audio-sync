#!/usr/bin/env python3
"""
Audio Backend - Multi-device PulseAudio/PipeWire control for Audio Sync Master.

Automatically detects ALL analog audio cards and ALL Bluetooth speakers,
creates a combined sink that plays to all devices simultaneously.

Architecture (PipeWire - Ubuntu 24.04+):
    [App Audio] -> [audio_master (module-combine-sink)]
                       |-> Analog Card 1
                       |-> BT Speaker 1
                       |-> ... (all detected devices)

Architecture (PulseAudio - Ubuntu 22.04):
    [App Audio] -> [audio_master (null sink)]
                       |-> loopback -> Analog Card 1 (configurable delay)
                       |-> loopback -> BT Speaker 1  (delay 1ms)

PipeWire uses module-combine-sink which handles routing natively without
the WirePlumber interference or clock-mismatch issues that plague loopbacks.

PulseAudio falls back to the null-sink + loopback approach with per-device delay.
"""

import subprocess
import re
import time
import threading
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from config import config, DEFAULT_ANALOG_DELAY


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run_cmd(*args, capture: bool = False) -> str | bool:
    """Run a shell command safely."""
    try:
        if capture:
            result = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout
        subprocess.run(
            list(args),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return "" if capture else False


def _split_module_blocks(modules_output: str) -> list[str]:
    """Split pactl list modules output into individual module blocks."""
    blocks = []
    current: list[str] = []
    for line in modules_output.splitlines():
        if line.startswith('Module #'):
            if current:
                blocks.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append('\n'.join(current))
    return blocks


def _find_loopback_module_ids() -> set[int]:
    """Find all module-loopback module IDs."""
    output = run_cmd('pactl', 'list', 'modules', capture=True)
    if not output:
        return set()
    ids: set[int] = set()
    for block in _split_module_blocks(output):
        id_match = re.search(r'Module #(\d+)', block)
        if id_match and 'Name: module-loopback' in block:
            ids.add(int(id_match.group(1)))
    return ids


def list_movable_sink_inputs() -> List[str]:
    """List sink input IDs excluding internal loopbacks, combine-sink, and EQ streams."""
    output = run_cmd('pactl', 'list', 'sink-inputs', capture=True)
    if not output:
        return []
    loopback_modules = _find_loopback_module_ids()
    # Also find combine-sink module IDs
    combine_modules = set()
    modules_output = run_cmd('pactl', 'list', 'modules', capture=True) or ''
    for block in _split_module_blocks(modules_output):
        id_match = re.search(r'Module #(\d+)', block)
        if id_match and 'Name: module-combine-sink' in block:
            combine_modules.add(int(id_match.group(1)))

    inputs: List[str] = []
    for block in output.split('Sink Input #')[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        input_id = lines[0].strip()
        owner_module = None
        driver = None
        app_name = None
        media_name = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('Owner Module:'):
                owner_module = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('Driver:'):
                driver = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('application.name ='):
                app_name = stripped.split('=', 1)[1].strip().strip('"')
            elif stripped.startswith('media.name ='):
                media_name = stripped.split('=', 1)[1].strip().strip('"')
        if owner_module and owner_module.isdigit():
            mod_id = int(owner_module)
            if mod_id in loopback_modules or mod_id in combine_modules:
                continue
        if driver and ('module-loopback' in driver or 'module-combine-sink' in driver):
            continue
        if app_name in ('module-loopback', 'module-ladspa-sink', 'module-combine-sink'):
            continue
        if media_name and ('Loopback' in media_name or 'Simultaneous' in media_name):
            continue
        inputs.append(input_id)
    return inputs


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OutputDevice:
    """Represents a detected audio output device."""
    sink_name: str
    description: str
    device_type: str  # "analog" or "bluetooth"
    is_synced: bool = False
    is_available: bool = True

    @property
    def display_name(self) -> str:
        """Human-readable name for the UI."""
        desc = self.description
        if not desc or desc == self.sink_name:
            if self.device_type == "bluetooth":
                parts = self.sink_name.split('.')
                if len(parts) >= 2:
                    return parts[1].replace('_', ':')
            return self.sink_name
        return desc

    @property
    def short_id(self) -> str:
        """Short stable identifier for config storage."""
        name = self.sink_name
        for prefix in ('alsa_output.', 'bluez_sink.', 'bluez_output.'):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name


@dataclass
class AudioState:
    """Current state of the entire audio system."""
    combine_sink_exists: bool = False
    combine_sink_module_id: Optional[int] = None
    default_sink: Optional[str] = None
    devices: List[OutputDevice] = field(default_factory=list)
    is_pipewire: bool = False

    @property
    def analog_devices(self) -> List[OutputDevice]:
        return [d for d in self.devices if d.device_type == "analog"]

    @property
    def bluetooth_devices(self) -> List[OutputDevice]:
        return [d for d in self.devices if d.device_type == "bluetooth"]

    @property
    def synced_devices(self) -> List[OutputDevice]:
        return [d for d in self.devices if d.is_synced]

    @property
    def unsynced_devices(self) -> List[OutputDevice]:
        return [d for d in self.devices if not d.is_synced and d.is_available]


# ---------------------------------------------------------------------------
# Audio Backend
# ---------------------------------------------------------------------------

class AudioBackend:
    """
    Multi-device audio backend for PulseAudio/PipeWire.

    On PipeWire (Ubuntu 24.04+): uses module-combine-sink for native multi-output.
    On PulseAudio (Ubuntu 22.04): uses module-null-sink + module-loopback with
        per-device delay control.
    """

    # PipeWire-safe flags for module-loopback (PulseAudio fallback mode)
    LOOPBACK_FLAGS = (
        'source_dont_move=true',
        'sink_dont_move=true',
        'adjust_time=0',
    )

    # WirePlumber state directory (Ubuntu 24.04 default)
    _WIREPLUMBER_STATE = Path.home() / '.local' / 'state' / 'wireplumber'

    def __init__(self):
        self.virtual_sink = config.get("virtual_sink")
        self._state = AudioState()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._on_device_change_callback = None
        self._lock = threading.Lock()
        self._is_pipewire = self._detect_pipewire()

    # --- Public API ---

    def get_state(self) -> AudioState:
        """Refresh and return current audio state."""
        with self._lock:
            self._refresh_state()
            return self._state

    def setup_sync(self) -> Tuple[bool, str]:
        """
        Idempotent setup of full audio sync.
        Detects all devices and creates combined sink or loopbacks as needed.
        Returns (success, message).
        """
        with self._lock:
            if self._is_pipewire:
                return self._setup_combine_sink_locked()
            else:
                return self._setup_loopback_locked()

    def set_analog_offset(self, sink_name: str, offset_ms: int) -> bool:
        """Set delay offset for an analog device.
        offset_ms: -150 to +150, applied on top of BASE_DELAY (121ms).
        Actual port-latency-offset = max(0, 121 + offset_ms) milliseconds.
        """
        offset_ms = max(-150, min(150, offset_ms))
        config.set_device_offset(sink_name, offset_ms)

        if self._is_pipewire:
            actual_ms = max(0, DEFAULT_ANALOG_DELAY + offset_ms)
            return self._set_port_latency_offset(sink_name, actual_ms)
        else:
            # PulseAudio: recreate loopback with absolute latency
            actual_ms = max(1, DEFAULT_ANALOG_DELAY + offset_ms)
            with self._lock:
                self._refresh_state()
                modules_output = run_cmd('pactl', 'list', 'modules', capture=True) or ''
                loopbacks = self._find_all_loopbacks(modules_output, sink_name)
                for module_id, _, _ in loopbacks:
                    run_cmd('pactl', 'unload-module', str(module_id))
                if loopbacks:
                    time.sleep(0.3)
                run_cmd(
                    'pactl', 'load-module', 'module-loopback',
                    f'source={self.virtual_sink}.monitor',
                    f'sink={sink_name}',
                    f'latency_msec={actual_ms}',
                    *self.LOOPBACK_FLAGS
                )
                return True

    def cleanup(self):
        """Remove all audio sync modules (only ours)."""
        self.stop_monitor()
        with self._lock:
            self._cleanup_locked()

    def factory_reset(self) -> Tuple[bool, str]:
        """
        Nuclear option: remove all modules, clear WirePlumber state,
        restart PipeWire services, and return to a clean state.
        """
        self.stop_monitor()
        with self._lock:
            self._cleanup_locked()

        # Clear WirePlumber state files
        state_dir = self._WIREPLUMBER_STATE
        if state_dir.is_dir():
            for f in state_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except OSError:
                        pass

        # Clear PulseAudio state files
        pulse_dir = Path.home() / '.config' / 'pulse'
        if pulse_dir.is_dir():
            for f in pulse_dir.iterdir():
                if f.is_file() and (f.suffix == '.tdb' or 'default-' in f.name):
                    try:
                        f.unlink()
                    except OSError:
                        pass

        # Restart audio services
        try:
            if self._is_pipewire:
                subprocess.run(
                    ['systemctl', '--user', 'restart', 'wireplumber', 'pipewire', 'pipewire-pulse'],
                    capture_output=True, timeout=10
                )
            else:
                subprocess.run(['pulseaudio', '--kill'], capture_output=True, timeout=5)
                time.sleep(1)
                subprocess.run(['pulseaudio', '--start'], capture_output=True, timeout=5)
        except FileNotFoundError:
            pass

        time.sleep(2)
        return (True, "Audio system reset complete. Click 'Resync All' to set up sync.")

    def move_streams_to_master(self):
        """Move all app audio streams to the virtual master sink."""
        target = self.virtual_sink
        sinks = run_cmd('pactl', 'list', 'sinks', 'short', capture=True) or ''
        if 'eq_sink' in sinks:
            target = 'eq_sink'
        for input_id in list_movable_sink_inputs():
            run_cmd('pactl', 'move-sink-input', input_id, target)

    def fix_loopback_routing(self):
        """Fix loopback sink-inputs misrouted by stream-restore (PulseAudio mode only)."""
        if self._is_pipewire:
            return  # combine-sink doesn't need routing fixes
        with self._lock:
            self._refresh_state()
            self._fix_loopback_routing_locked()

    # --- Device monitoring ---

    def start_monitor(self, on_change=None):
        """Start real-time device monitoring via pactl subscribe."""
        if self._monitor_running:
            return
        self._on_device_change_callback = on_change
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="device-monitor"
        )
        self._monitor_thread.start()

    def stop_monitor(self):
        """Stop device monitoring."""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
            self._monitor_thread = None

    def _monitor_loop(self):
        """Background thread: watch for sink add/remove events."""
        try:
            proc = subprocess.Popen(
                ['pactl', 'subscribe'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            while self._monitor_running and proc.poll() is None:
                line = proc.stdout.readline()
                if not line:
                    break
                if "'change' on sink" in line or "'new' on sink" in line or "'remove' on sink" in line:
                    time.sleep(0.5)
                    try:
                        self.setup_sync()
                        if self._on_device_change_callback:
                            self._on_device_change_callback()
                    except Exception:
                        pass
            proc.terminate()
        except Exception:
            pass

    # --- Internal: PipeWire combine-sink approach ---

    def _setup_combine_sink_locked(self) -> Tuple[bool, str]:
        """Setup audio sync using module-combine-sink (PipeWire mode)."""
        self._refresh_state()
        messages = []

        # Detect all current output devices
        device_sinks = [d.sink_name for d in self._state.devices if d.is_available]
        if not device_sinks:
            return (False, "No output devices detected")

        slaves_str = ','.join(device_sinks)

        # Check if combine-sink exists and has the right slaves
        if self._state.combine_sink_exists:
            current_slaves = self._get_combine_slaves()
            if set(current_slaves) == set(device_sinks):
                # Already correct, just ensure unmuted and routing
                run_cmd('pactl', 'set-sink-mute', self.virtual_sink, '0')
                # Mark devices as synced
                for d in self._state.devices:
                    if d.sink_name in device_sinks:
                        d.is_synced = True
                count = len(device_sinks)
                return (True, f"Audio sync active ({count} device{'s' if count != 1 else ''})")
            else:
                # Slaves changed (device added/removed), rebuild
                if self._state.combine_sink_module_id:
                    run_cmd('pactl', 'unload-module', str(self._state.combine_sink_module_id))
                    time.sleep(0.5)
                messages.append("Rebuilding sync for new devices")

        # Also clean up any ghost null-sink modules with our name
        self._cleanup_ghost_modules()

        # Create combine-sink with all detected devices as slaves
        result = run_cmd(
            'pactl', 'load-module', 'module-combine-sink',
            f'sink_name={self.virtual_sink}',
            f'slaves={slaves_str}',
            f'sink_properties=device.description="Audio_Master"'
        )

        if not result:
            return (False, "Failed to create combined sink")

        time.sleep(0.5)
        messages.append(f"Synced {len(device_sinks)} devices")

        # Set as default and unmute (don't touch volume levels)
        run_cmd('pactl', 'set-default-sink', self.virtual_sink)
        run_cmd('pactl', 'set-sink-mute', self.virtual_sink, '0')

        # Unmute all slave devices (don't force volume)
        for sink_name in device_sinks:
            run_cmd('pactl', 'set-sink-mute', sink_name, '0')

        # Restore EQ routing if enabled
        self._restore_eq_routing()

        # Move existing app streams
        self.move_streams_to_master()

        return (True, "; ".join(messages))

    def _get_combine_slaves(self) -> List[str]:
        """Get the current slaves of our combine-sink module."""
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if not modules_output:
            return []
        for block in _split_module_blocks(modules_output):
            id_match = re.search(r'Module #(\d+)', block)
            if not id_match or 'Name: module-combine-sink' not in block:
                continue
            arg_line = re.search(r'Argument: ([^\n]+)', block)
            if not arg_line:
                continue
            args = arg_line.group(1)
            if f'sink_name={self.virtual_sink}' not in args:
                continue
            slaves_match = re.search(r'slaves=(\S+)', args)
            if slaves_match:
                return slaves_match.group(1).split(',')
        return []

    # --- Internal: PulseAudio loopback approach (fallback) ---

    def _setup_loopback_locked(self) -> Tuple[bool, str]:
        """Setup audio sync using null-sink + loopbacks (PulseAudio mode)."""
        self._refresh_state()
        messages = []

        # 1. Create virtual sink if missing
        if not self._state.combine_sink_exists:
            null_sink_args = [
                'pactl', 'load-module', 'module-null-sink',
                f'sink_name={self.virtual_sink}',
            ]
            sys_spec = self._detect_system_sample_spec()
            if sys_spec:
                null_sink_args.extend(sys_spec)
            null_sink_args.append(
                f'sink_properties=device.description="Audio_Master"'
            )
            result = run_cmd(*null_sink_args)
            if result:
                messages.append("Created virtual master sink")
                time.sleep(0.5)
                self._refresh_state()
            else:
                return (False, "Failed to create virtual sink")

        # 2. Set virtual sink as default
        if self._state.default_sink != self.virtual_sink:
            run_cmd('pactl', 'set-default-sink', self.virtual_sink)

        run_cmd('pactl', 'set-sink-volume', self.virtual_sink, '100%')
        run_cmd('pactl', 'set-sink-mute', self.virtual_sink, '0')

        # 3. Create loopbacks for unsynced devices
        self._refresh_state()
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True) or ''

        for device in self._state.devices:
            if not device.is_available or device.is_synced:
                continue

            delay = config.get_device_delay(device.sink_name)
            run_cmd('pactl', 'set-sink-mute', device.sink_name, '0')
            run_cmd('pactl', 'set-sink-volume', device.sink_name, '100%')
            result = run_cmd(
                'pactl', 'load-module', 'module-loopback',
                f'source={self.virtual_sink}.monitor',
                f'sink={device.sink_name}',
                f'latency_msec={delay}',
                *self.LOOPBACK_FLAGS
            )
            if result:
                dtype = "BT" if device.device_type == "bluetooth" else "Analog"
                messages.append(f"Synced {dtype}: {device.display_name}")

        # 4. Restore EQ and move streams
        self._restore_eq_routing()
        self.move_streams_to_master()

        if not messages:
            count = len([d for d in self._state.devices if d.is_available])
            return (True, f"Audio sync active ({count} device{'s' if count != 1 else ''})")

        return (True, "; ".join(messages))

    # --- Internal: Shared helpers ---

    @staticmethod
    def _detect_pipewire() -> bool:
        """Detect if PipeWire is the audio server."""
        try:
            # pactl info shows "Server Name: PulseAudio (on PipeWire X.Y.Z)"
            result = subprocess.run(
                ['pactl', 'info'],
                capture_output=True, text=True, timeout=3
            )
            return 'PipeWire' in result.stdout
        except Exception:
            return False

    def _refresh_state(self):
        """Query PulseAudio/PipeWire for complete current state."""
        state = AudioState()
        state.is_pipewire = self._is_pipewire

        sinks_output = run_cmd('pactl', 'list', 'sinks', capture=True)
        sinks_short = run_cmd('pactl', 'list', 'sinks', 'short', capture=True) or ''

        if sinks_output:
            state.devices = self._detect_all_devices(sinks_output, sinks_short)

        info_output = run_cmd('pactl', 'info', capture=True)
        if info_output:
            match = re.search(r'Default Sink: (\S+)', info_output)
            if match:
                state.default_sink = match.group(1)

        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if modules_output:
            if self._is_pipewire:
                # Look for combine-sink
                for block in _split_module_blocks(modules_output):
                    id_match = re.search(r'Module #(\d+)', block)
                    if not id_match or 'Name: module-combine-sink' not in block:
                        continue
                    arg_line = re.search(r'Argument: ([^\n]+)', block)
                    if arg_line and f'sink_name={self.virtual_sink}' in arg_line.group(1):
                        state.combine_sink_exists = True
                        state.combine_sink_module_id = int(id_match.group(1))
                        # Mark devices that are in the combine slaves
                        slaves_match = re.search(r'slaves=(\S+)', arg_line.group(1))
                        if slaves_match:
                            slave_names = set(slaves_match.group(1).split(','))
                            for device in state.devices:
                                if device.sink_name in slave_names:
                                    device.is_synced = True
                        break
            else:
                # Look for null-sink + loopbacks
                null_id = self._find_module_id(
                    modules_output, 'module-null-sink', f'sink_name={self.virtual_sink}'
                )
                state.combine_sink_exists = null_id is not None
                state.combine_sink_module_id = null_id

                for device in state.devices:
                    loopbacks = self._find_all_loopbacks(modules_output, device.sink_name)
                    if loopbacks:
                        device.is_synced = True

        self._state = state

    def _detect_all_devices(self, sinks_output: str, sinks_short: str) -> List[OutputDevice]:
        """Detect all output devices from pactl output."""
        devices: List[OutputDevice] = []
        available_sink_names = set()

        for line in sinks_short.strip().splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                available_sink_names.add(parts[1].strip())

        for block in sinks_output.split('Sink #')[1:]:
            name_match = re.search(r'Name:\s*(\S+)', block)
            desc_match = re.search(r'Description:\s*(.+)', block)
            if not name_match:
                continue

            sink_name = name_match.group(1)
            description = desc_match.group(1).strip() if desc_match else sink_name

            # Skip our own virtual sinks
            if sink_name == self.virtual_sink:
                continue
            if sink_name == 'eq_sink' or 'ladspa' in sink_name.lower():
                continue

            # Classify device type
            if 'bluez_sink' in sink_name or 'bluez_output' in sink_name:
                device_type = "bluetooth"
            elif ('null' in sink_name or 'auto_null' in sink_name
                  or sink_name.startswith('tunnel.')):
                continue
            else:
                device_type = "analog"

            devices.append(OutputDevice(
                sink_name=sink_name,
                description=description,
                device_type=device_type,
                is_available=sink_name in available_sink_names
            ))

        return devices

    @staticmethod
    def _detect_system_sample_spec() -> List[str]:
        """Auto-detect the system's default sample specification."""
        try:
            info = run_cmd('pactl', 'info', capture=True)
            if info:
                spec_match = re.search(r'Default Sample Specification:\s*(\S+)\s+(\d+)ch\s+(\d+)Hz', info)
                if spec_match:
                    return [f'rate={spec_match.group(3)}', f'format={spec_match.group(1)}', f'channels={spec_match.group(2)}']
        except Exception:
            pass
        return ['rate=48000', 'format=float32le', 'channels=2']

    def _find_module_id(self, modules_output: str, module_name: str, arg_match: str) -> Optional[int]:
        """Find module ID by name and argument (block-safe)."""
        for block in _split_module_blocks(modules_output):
            id_match = re.search(r'Module #(\d+)', block)
            name_match = re.search(r'Name: (\S+)', block)
            arg_line = re.search(r'Argument: ([^\n]+)', block)
            if (id_match and name_match and arg_line
                    and name_match.group(1) == module_name
                    and arg_match in arg_line.group(1)):
                return int(id_match.group(1))
        return None

    def _find_all_loopbacks(self, modules_output: str, sink_name: str) -> List[Tuple[int, int, bool]]:
        """Find ALL loopback modules for a given sink from our virtual sink."""
        results: List[Tuple[int, int, bool]] = []
        for block in _split_module_blocks(modules_output):
            id_match = re.search(r'Module #(\d+)', block)
            if not id_match or 'Name: module-loopback' not in block:
                continue
            arg_line = re.search(r'Argument: ([^\n]+)', block)
            if not arg_line:
                continue
            args = arg_line.group(1)
            if f'sink={sink_name}' in args and f'source={self.virtual_sink}.monitor' in args:
                latency_match = re.search(r'latency_msec=(\d+)', args)
                latency = int(latency_match.group(1)) if latency_match else 10
                has_flags = all(flag.split('=')[0] in args for flag in self.LOOPBACK_FLAGS)
                results.append((int(id_match.group(1)), latency, has_flags))
        return results

    def _cleanup_ghost_modules(self):
        """Remove any ghost null-sink or combine-sink modules with our name."""
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if not modules_output:
            return
        for block in _split_module_blocks(modules_output):
            id_match = re.search(r'Module #(\d+)', block)
            name_match = re.search(r'Name: (\S+)', block)
            if not id_match or not name_match:
                continue
            module_name = name_match.group(1)
            if module_name not in ('module-null-sink', 'module-combine-sink'):
                continue
            arg_line = re.search(r'Argument: ([^\n]+)', block)
            if arg_line and f'sink_name={self.virtual_sink}' in arg_line.group(1):
                run_cmd('pactl', 'unload-module', str(id_match.group(1)))
        time.sleep(0.3)

    def _restore_eq_routing(self):
        """If EQ is enabled, restore routing through eq_sink."""
        sinks = run_cmd('pactl', 'list', 'sinks', 'short', capture=True) or ''
        if 'eq_sink' in sinks:
            run_cmd('pactl', 'set-default-sink', 'eq_sink')
            for input_id in list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, 'eq_sink')

    def _fix_loopback_routing_locked(self):
        """Fix loopback sink-inputs misrouted by stream-restore (PulseAudio only)."""
        sink_inputs_output = run_cmd('pactl', 'list', 'sink-inputs', capture=True)
        if not sink_inputs_output:
            return

        sinks_short = run_cmd('pactl', 'list', 'sinks', 'short', capture=True) or ''
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True) or ''

        sink_index_to_name: dict[str, str] = {}
        for line in sinks_short.strip().splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                sink_index_to_name[parts[0].strip()] = parts[1].strip()

        # Build module_id -> expected_sink from loopback args
        module_to_sink: dict[int, str] = {}
        for block in _split_module_blocks(modules_output):
            id_match = re.search(r'Module #(\d+)', block)
            if not id_match or 'Name: module-loopback' not in block:
                continue
            arg_line = re.search(r'Argument: ([^\n]+)', block)
            if not arg_line or f'source={self.virtual_sink}.monitor' not in arg_line.group(1):
                continue
            sink_match = re.search(r'sink=(\S+)', arg_line.group(1))
            if sink_match:
                module_to_sink[int(id_match.group(1))] = sink_match.group(1)

        sink_name_to_idx = {name: idx for idx, name in sink_index_to_name.items()}

        for block in sink_inputs_output.split('Sink Input #')[1:]:
            lines = block.strip().splitlines()
            if not lines:
                continue
            input_id = lines[0].strip()
            owner_module = None
            current_sink = None
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('Owner Module:'):
                    owner_module = stripped.split(':', 1)[1].strip()
                elif stripped.startswith('Sink:'):
                    current_sink = stripped.split(':', 1)[1].strip()
            if not owner_module or not current_sink or not owner_module.isdigit():
                continue
            module_id = int(owner_module)
            if module_id in module_to_sink:
                expected_sink = module_to_sink[module_id]
                expected_idx = sink_name_to_idx.get(expected_sink)
                if expected_idx and current_sink != expected_idx:
                    run_cmd('pactl', 'move-sink-input', input_id, expected_sink)

    def _cleanup_locked(self):
        """Remove all our modules and restore default sink (must hold lock)."""
        fallback_sink = self._find_fallback_sink()

        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
        if modules_output:
            # Remove all loopbacks from our virtual sink
            for block in _split_module_blocks(modules_output):
                id_match = re.search(r'Module #(\d+)', block)
                if not id_match or 'Name: module-loopback' not in block:
                    continue
                arg_line = re.search(r'Argument: ([^\n]+)', block)
                if arg_line and f'source={self.virtual_sink}.monitor' in arg_line.group(1):
                    run_cmd('pactl', 'unload-module', str(id_match.group(1)))

            # Remove all combine-sink and null-sink modules with our name
            for block in _split_module_blocks(modules_output):
                id_match = re.search(r'Module #(\d+)', block)
                name_match = re.search(r'Name: (\S+)', block)
                if not id_match or not name_match:
                    continue
                if name_match.group(1) not in ('module-null-sink', 'module-combine-sink'):
                    continue
                arg_line = re.search(r'Argument: ([^\n]+)', block)
                if arg_line and f'sink_name={self.virtual_sink}' in arg_line.group(1):
                    run_cmd('pactl', 'unload-module', str(id_match.group(1)))

        # Restore default sink to a real hardware device
        if fallback_sink:
            run_cmd('pactl', 'set-default-sink', fallback_sink)
            for input_id in list_movable_sink_inputs():
                run_cmd('pactl', 'move-sink-input', input_id, fallback_sink)

        time.sleep(0.5)

    def _set_port_latency_offset(self, sink_name: str, delay_ms: int) -> bool:
        """Set port latency offset for a PipeWire sink via pactl set-port-latency-offset."""
        sinks_output = run_cmd('pactl', 'list', 'sinks', capture=True)
        if not sinks_output:
            return False

        for block in sinks_output.split('Sink #')[1:]:
            name_match = re.search(r'Name:\s*(\S+)', block)
            if not name_match or name_match.group(1) != sink_name:
                continue

            # Find active port
            port_match = re.search(r'Active Port:\s*(\S+)', block)
            if not port_match:
                return False
            port_name = port_match.group(1)

            # Find card: PipeWire uses device.id in properties, PulseAudio uses Card: field
            card_idx = None
            card_match = re.search(r'^\s*Card:\s*#?(\d+)', block, re.MULTILINE)
            if card_match:
                card_idx = card_match.group(1)
            else:
                # PipeWire: device.id in properties
                devid_match = re.search(r'device\.id\s*=\s*"(\d+)"', block)
                if devid_match:
                    card_idx = devid_match.group(1)

            if not card_idx:
                return False

            # Resolve card name from index
            cards_short = run_cmd('pactl', 'list', 'cards', 'short', capture=True) or ''
            card_name = None
            for line in cards_short.strip().splitlines():
                parts = line.split('\t')
                if len(parts) >= 2 and parts[0].strip() == card_idx:
                    card_name = parts[1].strip()
                    break

            if not card_name:
                return False

            # Apply offset in microseconds
            offset_usec = delay_ms * 1000
            return bool(run_cmd(
                'pactl', 'set-port-latency-offset', card_name, port_name, str(offset_usec)
            ))

        return False

    def _find_fallback_sink(self) -> Optional[str]:
        """Find the first real hardware sink to use as fallback default."""
        sinks_output = run_cmd('pactl', 'list', 'sinks', capture=True)
        if not sinks_output:
            return None
        # Prefer analog first
        for block in sinks_output.split('Sink #')[1:]:
            name_match = re.search(r'Name:\s*(\S+)', block)
            if name_match and name_match.group(1).startswith('alsa_output.'):
                return name_match.group(1)
        # Then bluetooth
        for block in sinks_output.split('Sink #')[1:]:
            name_match = re.search(r'Name:\s*(\S+)', block)
            if name_match and name_match.group(1).startswith('bluez_'):
                return name_match.group(1)
        return None


# Global backend instance
audio = AudioBackend()
