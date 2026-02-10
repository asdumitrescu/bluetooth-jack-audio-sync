
import subprocess
import re
import sys

def run_cmd(args):
    return subprocess.run(args, capture_output=True, text=True).stdout

print("=== SINKS ===")
sinks = run_cmd(['pactl', 'list', 'sinks'])
for block in sinks.split('Sink #'):
    if 'Name:' not in block: continue
    name = re.search(r'Name: (\S+)', block).group(1)
    mute = re.search(r'Mute: (\S+)', block).group(1)
    vol = re.search(r'Volume: \S+ (\S+)', block)
    vol_val = vol.group(1) if vol else "?"
    print(f"Sink: {name}")
    print(f"  Mute: {mute}")
    print(f"  Volume: {vol_val}")
    if 'alsa_output' in name:
        port = re.search(r'Active Port: (\S+)', block)
        print(f"  Active Port: {port.group(1) if port else 'None'}")

print("\n=== SINK INPUTS (Streams) ===")
inputs = run_cmd(['pactl', 'list', 'sink-inputs'])
for block in inputs.split('Sink Input #'):
    if 'Sink:' not in block: continue
    sink_idx = re.search(r'Sink: (\d+)', block).group(1)
    mute = re.search(r'Mute: (\S+)', block).group(1)
    cvol = re.search(r'Volume: \S+ (\S+)', block).group(1)
    app = re.search(r'application.name = "([^"]+)"', block)
    media = re.search(r'media.name = "([^"]+)"', block)
    name = app.group(1) if app else (media.group(1) if media else "Unknown")
    
    print(f"Input on Sink #{sink_idx}: {name}")
    print(f"  Mute: {mute}")
    print(f"  Volume: {cvol}")
    if 'Loopback' in name:
         print(f"  (This looks like a loopback)")

print("\n=== MODULES (Loopbacks) ===")
modules = run_cmd(['pactl', 'list', 'modules'])
for block in modules.split('Module #'):
    if 'module-loopback' in block:
        args = re.search(r'Argument: (.+)', block).group(1)
        print(f"Loopback Module: {args}")
