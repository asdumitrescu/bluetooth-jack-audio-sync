
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.getcwd())

try:
    from audio_backend import audio
    print("Testing setup_sync...")
    success, message = audio.setup_sync()
    print(f"Success: {success}")
    print(f"Message: {message}")
    
    # Check state
    state = audio.get_state()
    print(f"Virtual Sink: {state.virtual_sink_exists}")
    print(f"Jack Loopback: {state.jack_loopback_exists}")
    print(f"Jack Sink Used: {audio.jack_sink}")
    print(f"BT Loopback: {state.bt_loopback_exists}")
    print(f"BT Sink Name: {state.bt_sink_name}")
    
except Exception as e:
    print(f"Error: {e}")
