#!/usr/bin/env python3
"""
Shared PulseAudio/PipeWire utilities for Audio Sync Master.
All pactl subprocess calls go through here with proper error handling.
"""

import subprocess
import re
import threading
from typing import Optional, List, Tuple

# Thread lock for all pactl operations
_pactl_lock = threading.Lock()


def is_pipewire() -> bool:
    """Detect if PipeWire is running as the audio server."""
    output = run_cmd('pactl', 'info', capture=True)
    if output:
        return 'PipeWire' in output
    return False


def run_cmd(*args, capture: bool = False, timeout: int = 5) -> str | bool:
    """Run a shell command safely with thread locking."""
    with _pactl_lock:
        try:
            if capture:
                result = subprocess.run(
                    list(args),
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                return result.stdout
            result = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return "" if capture else False


def run_cmd_unlocked(*args, capture: bool = False, timeout: int = 5) -> str | bool:
    """Run a command without acquiring the lock (for use when lock is already held)."""
    try:
        if capture:
            result = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout
        result = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "" if capture else False


# Sink name validation regex: only safe characters
_SAFE_SINK_RE = re.compile(r'^[a-zA-Z0-9_.:-]+$')


def validate_sink_name(name: str) -> bool:
    """Validate a PulseAudio sink name contains only safe characters."""
    return bool(_SAFE_SINK_RE.match(name))


def sanitize_sink_name(name: str) -> str:
    """Strip unsafe characters from a sink name."""
    return re.sub(r'[^a-zA-Z0-9_.:-]', '', name)


def parse_module_blocks(modules_output: str) -> list[dict]:
    """
    Parse `pactl list modules` output into structured blocks.
    Fixes the DOTALL regex issue by parsing block-by-block.
    """
    blocks = []
    for raw_block in modules_output.split('Module #')[1:]:
        lines = raw_block.strip().splitlines()
        if not lines:
            continue
        block = {
            'id': None,
            'name': '',
            'argument': '',
        }
        # First line is the module ID
        id_str = lines[0].strip()
        if id_str.isdigit():
            block['id'] = int(id_str)

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith('Name:'):
                block['name'] = stripped.split(':', 1)[1].strip()
            elif stripped.startswith('Argument:'):
                block['argument'] = stripped.split(':', 1)[1].strip()

        blocks.append(block)
    return blocks


def find_module_id(modules_output: str, module_name: str, arg_match: str) -> Optional[int]:
    """Find module ID by name and argument substring (block-safe)."""
    for block in parse_module_blocks(modules_output):
        if block['name'] == module_name and arg_match in block['argument']:
            return block['id']
    return None


def find_loopback_info(modules_output: str, sink_name: str, source_monitor: str) -> Optional[Tuple[int, int]]:
    """Find loopback module ID and latency for a given sink (block-safe)."""
    for block in parse_module_blocks(modules_output):
        if block['name'] != 'module-loopback':
            continue
        args = block['argument']
        if f'sink={sink_name}' in args and f'source={source_monitor}' in args:
            latency_match = re.search(r'latency_msec=(\d+)', args)
            latency = int(latency_match.group(1)) if latency_match else 10
            return (block['id'], latency)
    return None


def find_loopback_module_ids(modules_output: Optional[str] = None) -> set[int]:
    """Find all loopback module IDs."""
    if modules_output is None:
        modules_output = run_cmd('pactl', 'list', 'modules', capture=True)
    if not modules_output:
        return set()
    ids: set[int] = set()
    for block in parse_module_blocks(modules_output):
        if block['name'] == 'module-loopback' and block['id'] is not None:
            ids.add(block['id'])
    return ids


def list_movable_sink_inputs() -> List[str]:
    """List sink inputs excluding internal loopbacks to keep sync stable."""
    output = run_cmd('pactl', 'list', 'sink-inputs', capture=True)
    if not output:
        return []
    loopback_modules = find_loopback_module_ids()
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
        if owner_module and owner_module.isdigit() and int(owner_module) in loopback_modules:
            continue
        if driver and 'module-loopback' in driver:
            continue
        if app_name in ('module-loopback', 'module-ladspa-sink'):
            continue
        if media_name and 'Loopback' in media_name:
            continue
        inputs.append(input_id)
    return inputs


def list_sinks() -> list[dict]:
    """List all audio sinks with name, description, and type."""
    output = run_cmd('pactl', 'list', 'sinks', capture=True)
    if not output:
        return []
    sinks = []
    for block in output.split('Sink #')[1:]:
        sink = {'name': '', 'description': '', 'is_bluetooth': False, 'is_analog': False}
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith('Name:'):
                sink['name'] = stripped.split(':', 1)[1].strip()
                sink['is_bluetooth'] = 'bluez' in sink['name'].lower()
                sink['is_analog'] = 'analog' in sink['name'].lower()
            elif stripped.startswith('Description:'):
                sink['description'] = stripped.split(':', 1)[1].strip()
            elif 'device.description =' in stripped:
                desc = stripped.split('=', 1)[1].strip().strip('"')
                if desc:
                    sink['description'] = desc
        if sink['name']:
            sinks.append(sink)
    return sinks


def find_bt_sink_by_description(speaker_name: str) -> Optional[str]:
    """
    Find Bluetooth sink by matching device.description or Description field.
    More precise than substring match on the whole block.
    """
    output = run_cmd('pactl', 'list', 'sinks', capture=True)
    if not output:
        return None
    for block in output.split('Sink #')[1:]:
        sink_name = None
        description = ''
        device_desc = ''
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith('Name:'):
                val = stripped.split(':', 1)[1].strip()
                if 'bluez' in val.lower():
                    sink_name = val
            elif stripped.startswith('Description:'):
                description = stripped.split(':', 1)[1].strip()
            elif 'device.description =' in stripped:
                device_desc = stripped.split('=', 1)[1].strip().strip('"')
        if sink_name and (speaker_name in description or speaker_name in device_desc):
            return sink_name
    return None


def auto_detect_analog_sink() -> Optional[str]:
    """Auto-detect the first available wired output sink (analog or HDMI)."""
    # Prefer analog first
    for sink in list_sinks():
        if sink['is_analog'] and not sink['is_bluetooth']:
            return sink['name']
    # Fall back to any non-BT ALSA sink (e.g. HDMI)
    for sink in list_sinks():
        if not sink['is_bluetooth'] and sink['name'].startswith('alsa_output'):
            return sink['name']
    return None


def auto_detect_bt_sink() -> Optional[str]:
    """Auto-detect the first available Bluetooth output sink."""
    for sink in list_sinks():
        if sink['is_bluetooth']:
            return sink['name']
    return None


def get_bt_codec_info() -> Optional[dict]:
    """Get Bluetooth codec and latency information."""
    output = run_cmd('pactl', 'list', 'sinks', capture=True)
    if not output:
        return None
    for block in output.split('Sink #')[1:]:
        if 'bluez' not in block.lower():
            continue
        info = {'codec': 'SBC', 'latency_us': 0, 'latency_ms': 0}
        for line in block.splitlines():
            stripped = line.strip()
            if 'bluetooth.codec' in stripped or 'api.bluez5.codec' in stripped:
                val = stripped.split('=', 1)[1].strip().strip('"')
                info['codec'] = val
            elif stripped.startswith('Latency:'):
                match = re.search(r'(\d+)\s+usec', stripped)
                if match:
                    info['latency_us'] = int(match.group(1))
                    info['latency_ms'] = info['latency_us'] // 1000
        return info
    return None


# Suggested delay offsets by codec (approximate)
CODEC_DELAY_HINTS = {
    'SBC': 130,
    'AAC': 150,
    'aptX': 60,
    'aptX_HD': 70,
    'aptx': 60,
    'aptx_hd': 70,
    'LDAC': 200,
    'ldac': 200,
}


def suggest_jack_delay(codec: str) -> int:
    """Suggest a Jack delay based on Bluetooth codec."""
    for key, delay in CODEC_DELAY_HINTS.items():
        if key.lower() in codec.lower():
            return delay
    return 115  # default
