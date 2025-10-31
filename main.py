import asyncio
import os
import time
import json
import base64
import ctypes
import webrtcvad
import alsaaudio
import websockets
from collections import deque
import random
import signal
import atexit
import wave
import threading
import subprocess
import requests
from typing import Optional, Dict
from mute_button import start_mute_button, is_muted, stop_mute_button, set_state_check, set_mode
import serial_com
import nfc_backend
from dotenv import load_dotenv

# ── ALSA: suppress warnings ───────────────────────────────────────────────
ctypes.CDLL('libasound.so').snd_lib_error_set_handler(None)

# ── Load environment variables from tmpfs (RAM-based storage) ─────────────
# Config fetcher writes to /tmp/aiflow.env on boot
load_dotenv('/tmp/aiflow.env')

# ==========================================================================
# CONFIGURATION VARIABLES –– tweak here
# ==========================================================================

# API / WebSocket
API_KEY  = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")
WS_ENDPOINT = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT_ID}"
detail = False #set to true for detailed logging
# Devices
MIC_DEVICE = os.getenv("MIC_DEVICE", "plughw:0,0")
SPK_DEVICE = os.getenv("SPK_DEVICE", "plughw:0,0")

# Audio constants
RATE = 16000
CHANNELS = 1
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
FRAME_MS = 30
FRAME_SEC = FRAME_MS / 1000.0
SAMPLES_PER_FRAME = RATE * FRAME_MS // 1000     # 480 @ 16kHz
BYTES_PER_SAMPLE = 2
FRAME_BYTES = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE
PERIODSIZE = SAMPLES_PER_FRAME

# VAD parameters
VAD_MODE = 3                    # 0..3 (3 = most aggressive)
MIN_SPOKEN_MS = 800            # minimum speech before valid (ms)
SILENCE_END_MS = 1500           # silence to trigger end (ms)
PREROLL_FRAMES = 5
START_GATE_FRAMES = 8           # Require 5 consecutive speech frames (150ms) to trigger

# Derived from above
MIN_CHUNKS = MIN_SPOKEN_MS // FRAME_MS
END_SILENCE_CHUNKS = SILENCE_END_MS // FRAME_MS

# Response handling
FIRST_CONTENT_MAX = 5.0         # sec to wait for first agent response
CONTENT_IDLE      = 0.15         # sec idle after last content
GRACE_DRAIN       = 0.15         # sec sweep for stragglers
FIRST_TURN_BARGE_AFTER_MS = 500 # open mic ~0.6s after greeting starts

# Input mode: "VAD" (hands-free) or "PTT" (push-to-talk)
INPUT_MODE = os.getenv("INPUT_MODE", "PTT").upper()

# Basic activity timestamps (optional/useful for debugging)
LAST_WS_ACTIVITY_TS = time.time()
LAST_USER_MSG_TS = time.time()

# ==========================================================================
# INTERNAL STATE
# ==========================================================================

# Application state machine
STATE = "splash_idle"  # splash_idle | running_agent
STATE_LOCK = threading.Lock()

VAD = webrtcvad.Vad(VAD_MODE)
t0 = time.time()
_OPEN_PCMS = {}
LAST_MIC_METRICS = None
STOP = False

# NFC reader will be initialized after on_nfc_tag_detected is defined
nfc = None
NFC_TAGS_URL = "https://raw.githubusercontent.com/CollaboratorFuturity/futuresGarden/main/nfc_tags.json"

# Global event loop reference for thread-safe async task scheduling
MAIN_EVENT_LOOP = None

# ==========================================================================
# HELPERS
# ==========================================================================

def log(msg):
    print(f"[{time.time()-t0:7.3f}s] {msg}")

# Start mute button - behavior depends on INPUT_MODE
start_mute_button(pin="D12", debounce_s=0.5)  # GPIO12 to GND, internal pull-up

# Configure button to only work during running_agent state
set_state_check(lambda: get_state() == "running_agent")

log(f"🎤 Input mode: {INPUT_MODE}")
if INPUT_MODE == "PTT":
    log("📍 PTT Mode: Press and hold button to talk, release to end turn")
else:
    log("📍 VAD Mode: Voice activity detection with optional mute button")

# Set button mode after starting the button
set_mode(INPUT_MODE)


def track_pcm(pcm):
    _OPEN_PCMS[id(pcm)] = pcm
    return pcm

def safe_close(pcm, label="pcm"):
    if pcm:
        try: pcm.close()
        except Exception as e: log(f"⚠️ close({label}) error: {e}")
        _OPEN_PCMS.pop(id(pcm), None)

def safe_close_all():
    for pcm in list(_OPEN_PCMS.values()):
        try: pcm.close()
        except Exception: pass
    _OPEN_PCMS.clear()

async def post_close_grace():
    await asyncio.sleep(0.05)

def _cleanup_on_exit():
    """Ensure clean shutdown with 'B' signal on any exit."""
    serial_com.write('B')
    safe_close_all()

atexit.register(_cleanup_on_exit)

def setup_mic(device=MIC_DEVICE):
    return track_pcm(alsaaudio.PCM(
        type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NONBLOCK,
        device=device, channels=CHANNELS, rate=RATE,
        format=FORMAT, periodsize=PERIODSIZE
    ))

def setup_speaker(device=SPK_DEVICE):
    return track_pcm(alsaaudio.PCM(
        type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL,
        device=device, channels=CHANNELS, rate=RATE,
        format=FORMAT, periodsize=PERIODSIZE
    ))

def is_speech_exact(frame_bytes: bytes) -> bool:
    return len(frame_bytes) == FRAME_BYTES and VAD.is_speech(frame_bytes, RATE)

# >>> Added: helper to send user messages and update keepalive timer
async def send_user_json(ws, obj: dict):
    """Send a JSON message and record 'user message' if applicable."""
    await ws.send(json.dumps(obj))
    # Count 'user_*' keys and explicit user_activity as user messages
    if obj.get("type") == "user_activity" or any(str(k).startswith("user_") for k in obj.keys()):
        record_user_message()

def set_idle(is_idle: bool):
    """Mark whether the app is idle (no mic turn; no agent speaking)."""
    global _idle
    _idle = bool(is_idle)

def record_user_message():
    """Tell the keepalive that a user-level message was just sent."""
    global _last_user_msg_ts
    _last_user_msg_ts = time.monotonic()

async def send_pong(ws, event_id, delay_ms=0):
    try:
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        msg = {"type": "pong", "event_id": event_id}
        await ws.send(json.dumps(msg))
        #log(f"🏓 Sent ElevenLabs pong for event_id={event_id}")
    except websockets.exceptions.ConnectionClosed:
        # Socket closed, pong is irrelevant now - don't log noise
        pass
    except Exception as e:
        log(f"⚠️ Failed to send pong: {e}")

async def maintain_pong(ws):
    """Keeps the ElevenLabs websocket alive between turns when idle."""
    last_keepalive = time.time()
    KEEPALIVE_INTERVAL = 60.0  # Send user_activity keepalive every 60s to prevent timeout

    while True:
        try:
            # Send periodic keepalive to prevent server timeout
            now = time.time()
            if now - last_keepalive >= KEEPALIVE_INTERVAL:
                try:
                    await ws.send(json.dumps({"type": "user_activity"}))
                    last_keepalive = now
                    #log("🏓 Sent periodic user_activity keepalive")
                except websockets.exceptions.ConnectionClosed:
                    # Socket closed, will exit on next recv()
                    pass
                except Exception:
                    # Ignore send errors, will be caught by recv()
                    pass

            # Wait at most 5 seconds for new messages, so we can shut down cleanly
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(raw)
            if data.get("type") == "ping":
                event = data.get("ping_event", {})
                event_id = event.get("event_id")
                ping_ms = event.get("ping_ms") or 0
                asyncio.create_task(send_pong(ws, event_id, ping_ms))
                #log(f"🏓 [idle] ElevenLabs ping received (event_id={event_id}) — pong scheduled")
        except asyncio.TimeoutError:
            # No message for a few seconds → loop again
            continue
        except asyncio.CancelledError:
            #log("🔌 Idle keepalive cancelled.")
            break
        except websockets.exceptions.ConnectionClosed:
            log("🔌 Idle keepalive stopped (socket closed).")
            break
        except Exception as e:
            log(f"⚠️ Idle keepalive error: {e}")
            await asyncio.sleep(1)

# ==========================================================================
# STATE MANAGEMENT
# ==========================================================================

def get_state():
    """Get current application state."""
    with STATE_LOCK:
        return STATE

def set_state(new_state):
    """Set application state."""
    global STATE
    with STATE_LOCK:
        old = STATE
        STATE = new_state
        if old != new_state:
            log(f"🔄 State transition: {old} → {new_state}")

def play_beep():
    """
    Play a short beep sound when NFC tag is scanned.
    Looks for beep.wav in: /home/orb/AIflow/beep.wav
    """
    serial_com.write('L')  # Show loading animation for all NFC scans
    beep_path = "/home/orb/AIflow/beep.wav"
    
    if not os.path.exists(beep_path):
        # No beep file, silently skip
        return
    
    try:
        with wave.open(beep_path, 'rb') as wf:
            # Quick validation
            if wf.getframerate() != RATE or wf.getnchannels() != 1:
                log(f"⚠️ Beep file must be {RATE}Hz mono")
                return
            
            speaker = setup_speaker()
            try:
                # Play the beep (should be short!)
                while True:
                    data = wf.readframes(480)
                    if not data:
                        break
                    if len(data) < FRAME_BYTES:
                        data += b'\x00' * (FRAME_BYTES - len(data))
                    speaker.write(data)
            finally:
                safe_close(speaker, "beep_speaker")
    except Exception as e:
        log(f"⚠️ Beep playback error: {e}")

def on_nfc_tag_detected(tag_name: str):
    """
    Callback from NFC backend when a tag is scanned.
    Routes to appropriate state based on tag name.
    Only called for known tags from the JSON file.
    """
    tag_name = tag_name.strip().upper()

    # Play beep sound for NFC scan feedback (also sends 'L' for loading animation)
    play_beep()

    current_state = get_state()

    if tag_name == "TEST":
        # TEST tag = hot reload configuration from API (fast: 2-3s)
        log("🔄 TEST tag scanned - initiating hot reload...")

        # Schedule hot reload using thread-safe method
        if MAIN_EVENT_LOOP:
            asyncio.run_coroutine_threadsafe(hot_reload_config(), MAIN_EVENT_LOOP)
        else:
            log("❌ Hot reload failed: Event loop not available")

    elif tag_name == "AGENT_START":
        set_state("running_agent")
    else:
        # Known tag but not TEST or AGENT_START - show NFC animation
        serial_com.write('N')
        log(f"🪪 NFC: {tag_name}")

        # If we're in running_agent state, force current turn to end so agent can respond
        if current_state == "running_agent":
            import mute_button as mb
            mb.force_turn_end.set()

# ==========================================================================
# STARTUP TEST AUDIO
# ==========================================================================

def play_startup_test_audio():
    """
    Plays startup test audio (synchronous, non-async).
    Called before entering splash_idle to confirm audio is working.
    File location: /home/orb/AIflow/{AGENT_ID}/test.wav
    """
    log("🎵 Playing startup test audio...")
    serial_com.write('L')  # Show loading animation during test audio

    # Test audio file path: /home/orb/AIflow/{AGENT_ID}/test.wav
    test_audio_path = os.path.join("/home/orb/AIflow", AGENT_ID, "test.wav")

    try:
        if not os.path.exists(test_audio_path):
            log(f"⚠️ Test audio file not found: {test_audio_path}")
            log(f"💡 To enable startup test audio, place a 16kHz mono WAV file at:")
            log(f"   {test_audio_path}")
            return

        # Open and validate WAV file
        with wave.open(test_audio_path, 'rb') as wf:
            # Validate format
            if wf.getnchannels() != 1:
                log(f"⚠️ Test audio must be mono (found {wf.getnchannels()} channels)")
                return
            if wf.getframerate() != RATE:
                log(f"⚠️ Test audio must be {RATE}Hz (found {wf.getframerate()}Hz)")
                return
            if wf.getsampwidth() != 2:
                log(f"⚠️ Test audio must be 16-bit (found {wf.getsampwidth()*8}-bit)")
                return

            log(f"✓ WAV format validated: {wf.getframerate()}Hz, {wf.getnchannels()}ch, {wf.getsampwidth()*8}-bit")

            # Open speaker
            speaker = setup_speaker()

            try:
                # Read and play the WAV audio (480 frames = SAMPLES_PER_FRAME)
                while True:
                    data = wf.readframes(480)
                    if not data:
                        break

                    # Pad last chunk if needed
                    if len(data) < FRAME_BYTES:
                        data += b'\x00' * (FRAME_BYTES - len(data))

                    speaker.write(data)

                log("✅ Startup test audio playback complete")

            finally:
                safe_close(speaker, "test_speaker")

    except Exception as e:
        log(f"⚠️ Startup test audio error: {e}")

# ==========================================================================
# HOT RELOAD CONFIGURATION
# ==========================================================================

# Agent name to ID mapping (copied from config_fetcher.py)
AGENT_NAME_TO_ID = {
    "Zane": "uHlKfBtzRYokBFLcCOjq",
    "Rowan": "agent_01jvs5f45jepab76tr81m51gdx",
    "Nova": "agent_1701k5bgdzmte5f9q518mge3jsf0",
    "Cypher": "agent_01jvwd88bdeeftgh3kxrx1k4sk"
}

# Volume lookup table (copied from config_fetcher.py)
VOLUME_MAP = {
    10: 124,  # 100%
    9: 121,   # 89%
    8: 118,   # 79%
    7: 114,   # 71%
    6: 110,   # 61%
    5: 104,   # 52%
    4: 96,    # 41%
    3: 85,    # 30%
    2: 65,    # 20%
    1: 0      # 9%
}

async def hot_reload_config() -> bool:
    """
    Hot reload configuration from Supabase API without restarting the process.
    Updates AGENT_ID, volume, and reconnects WebSocket if needed.

    This is much faster than full restart (2-3s vs 15-90s) and preserves:
    - Process state and PID
    - Log continuity
    - Hardware initialization (GPIO, I2C, NFC)
    - No test audio replay

    Returns:
        True if successful, False on error
    """
    log("🔄 Hot reload: Fetching fresh configuration from API...")
    serial_com.write('L')  # Show loading animation

    # Get DEVICE_ID from environment
    device_id = os.getenv("DEVICE_ID")
    if not device_id:
        log("❌ Hot reload failed: DEVICE_ID not set in environment")
        serial_com.write('S' if get_state() == "splash_idle" else 'L')
        return False

    # Construct API URL (same as config_fetcher.py)
    api_url = f"https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/get-device-config?device_id={device_id}"

    try:
        # Fetch configuration with retry logic (5 attempts, 10s timeout each)
        config = None
        for attempt in range(1, 6):
            try:
                log(f"📡 API request (attempt {attempt}/5)...")
                response = requests.get(api_url, timeout=10)
                response.raise_for_status()
                config = response.json()
                log("✅ Configuration fetched successfully")
                break
            except requests.RequestException as e:
                log(f"⚠️ API request failed: {e}")
                if attempt < 5:
                    log(f"⏳ Retrying in 2 seconds...")
                    await asyncio.sleep(2)
                else:
                    log("❌ Max retries reached")
                    serial_com.write('S' if get_state() == "splash_idle" else 'L')
                    return False

        if not config:
            log("❌ Hot reload failed: No config received")
            serial_com.write('S' if get_state() == "splash_idle" else 'L')
            return False

        # Extract values from config
        agent_name = config.get("agent_id")
        volume = config.get("volume")
        input_mode = config.get("input_mode", "PTT").upper()

        if not agent_name:
            log("❌ Hot reload failed: No agent_id in config")
            serial_com.write('S' if get_state() == "splash_idle" else 'L')
            return False

        # Map agent name to ID
        new_agent_id = AGENT_NAME_TO_ID.get(agent_name)
        if not new_agent_id:
            log(f"❌ Hot reload failed: Unknown agent name '{agent_name}'")
            log(f"   Available: {list(AGENT_NAME_TO_ID.keys())}")
            serial_com.write('S' if get_state() == "splash_idle" else 'L')
            return False

        log(f"🎭 Agent: {agent_name} → {new_agent_id}")

        # Update volume if provided
        if volume is not None:
            try:
                volume_int = int(volume)
                if volume_int in VOLUME_MAP:
                    raw_value = VOLUME_MAP[volume_int]
                    log(f"🔊 Setting volume to {volume_int}/10 (raw: {raw_value})...")

                    # Use amixer to set volume (requires ALSA)
                    result = subprocess.run(
                        ["amixer", "set", "Speaker", str(raw_value)],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )

                    if result.returncode == 0:
                        log(f"✅ Volume updated to {volume_int}/10")
                    else:
                        log(f"⚠️ Volume update failed: {result.stderr}")
                else:
                    log(f"⚠️ Invalid volume value: {volume_int} (must be 1-10)")
            except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                log(f"⚠️ Volume update error: {e}")

        # Check if AGENT_ID or INPUT_MODE changed
        global AGENT_ID, WS_ENDPOINT, INPUT_MODE
        old_agent_id = AGENT_ID
        old_input_mode = INPUT_MODE
        agent_changed = (new_agent_id != old_agent_id)
        input_mode_changed = (input_mode != old_input_mode)

        if agent_changed:
            log(f"✅ Agent changed: {old_agent_id} → {new_agent_id}")
            AGENT_ID = new_agent_id
            WS_ENDPOINT = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT_ID}"
            os.environ["AGENT_ID"] = AGENT_ID
        else:
            log(f"ℹ️  Agent unchanged ({agent_name})")

        # Update INPUT_MODE if changed
        if input_mode_changed:
            log(f"✅ Input mode changed: {old_input_mode} → {input_mode}")
            INPUT_MODE = input_mode
            os.environ["INPUT_MODE"] = INPUT_MODE

            # Reconfigure button for new mode
            import mute_button
            mute_button.set_mode(INPUT_MODE)
        else:
            log(f"ℹ️  Input mode unchanged ({input_mode})")

        # Write updated config to /tmp/aiflow.env (if anything changed)
        if agent_changed or input_mode_changed:
            try:
                with open("/tmp/aiflow.env", "w") as f:
                    f.write(f"AGENT_ID={AGENT_ID}\n")
                    f.write(f"ELEVENLABS_API_KEY={os.getenv('ELEVENLABS_API_KEY')}\n")
                    f.write(f"INPUT_MODE={INPUT_MODE}\n")
                    if volume is not None:
                        f.write(f"VOLUME={volume}\n")
                log("✅ Updated /tmp/aiflow.env")
            except Exception as e:
                log(f"⚠️ Failed to write /tmp/aiflow.env: {e}")

        # ALWAYS force return to splash_idle (whether agent changed or not)
        current_state = get_state()
        if current_state == "running_agent":
            log("🔄 Exiting conversation to return to splash...")
            set_state("splash_idle")
            await asyncio.sleep(0.5)

        # ALWAYS play test audio (whether agent changed or not)
        log("🎵 Playing test audio after hot reload...")
        test_audio_path = os.path.join("/home/orb/AIflow", AGENT_ID, "test.wav")

        try:
            if os.path.exists(test_audio_path):
                with wave.open(test_audio_path, 'rb') as wf:
                    # Validate format
                    if wf.getnchannels() == 1 and wf.getframerate() == RATE and wf.getsampwidth() == 2:
                        speaker = setup_speaker()
                        try:
                            # Play test audio
                            while True:
                                data = wf.readframes(480)
                                if not data:
                                    break
                                if len(data) < FRAME_BYTES:
                                    data += b'\x00' * (FRAME_BYTES - len(data))
                                speaker.write(data)
                            log("✅ Test audio playback complete")
                        finally:
                            safe_close(speaker, "reload_test_speaker")
                    else:
                        log(f"⚠️ Test audio skipped (invalid format)")
            else:
                log(f"⚠️ Test audio file not found: {test_audio_path}")
        except Exception as audio_err:
            log(f"⚠️ Test audio playback error: {audio_err}")

        # ALWAYS return to splash screen
        serial_com.write('S')
        log("✅ Hot reload complete - scan AGENT_START to begin conversation")
        return True

    except Exception as e:
        log(f"❌ Hot reload error: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        serial_com.write('S' if get_state() == "splash_idle" else 'L')
        return False

# ==========================================================================
# TURN METRICS
# ==========================================================================

class TurnMetrics:
    def __init__(self): self.reset()
    def reset(self):
        self.start_ts = time.time()
        self.frames_sent = self.bytes_sent = self.ms_sent = 0
        self.zero_len_reads = self.user_transcripts = 0
        self.voiced_frames_sent = self.unvoiced_frames_sent = 0
        self.agent_first_content_ts = None
        self.agent_text_chunks = self.agent_text_chars = 0
        self.agent_audio_bytes = 0
        self.synthetic_ms_sent = 0.0
    def on_audio_sent(self, n, voiced=None, synthetic=False):
        self.frames_sent+=1; self.bytes_sent+=n
        delta_ms=(n/(RATE*BYTES_PER_SAMPLE))*1000.0
        self.ms_sent+=delta_ms
        if synthetic:
            self.synthetic_ms_sent+=delta_ms
        if voiced is True: self.voiced_frames_sent+=1
        elif voiced is False: self.unvoiced_frames_sent+=1
    def on_zero_len_read(self): self.zero_len_reads+=1
    def on_agent_content(self):
        if self.agent_first_content_ts is None:
            self.agent_first_content_ts=time.time()
    def on_agent_text(self,text):
        self.on_agent_content(); self.agent_text_chunks+=1
        self.agent_text_chars+=len(text or "")
    def on_agent_audio(self,n):
        self.on_agent_content(); self.agent_audio_bytes+=n
    def on_user_transcript(self): self.user_transcripts+=1

# ==========================================================================
# MIC TURN
# ==========================================================================

async def stream_audio(ws):
    """
    Stream audio from microphone to ElevenLabs.
    Behavior depends on INPUT_MODE: VAD (voice activity) or PTT (push-to-talk).
    """
    if INPUT_MODE == "PTT":
        await stream_audio_ptt(ws)
    else:
        await stream_audio_vad(ws)

async def stream_audio_ptt(ws):
    """
    PTT Mode: Push-to-talk with button control.
    - Press button → start sending immediately
    - Release button → end turn immediately
    - No VAD, no silence detection
    """
    # Force button to muted state before starting
    import mute_button as mb
    mb.force_mute()
    
    mic = setup_mic(); metrics = TurnMetrics()
    buf = bytearray(); speaking = False
    nfc_triggered = False  # Track if NFC caused exit

    # Track previous mute state for edge detection
    # Force True to ensure first button press is always detected as edge
    prev_muted = True

    # Mark idle while waiting for button press
    set_idle(True)
    log("🎙️ [PTT] Press and hold button to talk...")
    serial_com.write('M')  # Always start with mute indicator
    
    nfc.enable()
    
    try:
        while True:
            if STOP or get_state() != "running_agent":
                log("🛑 PTT audio streaming stopped (STOP or state changed)")
                return

            # Check if WebSocket is still alive
            try:
                if ws.protocol.state.name != "OPEN":
                    log("🔌 WebSocket closed while waiting for button - exiting")
                    return
            except Exception:
                # If we can't check state, socket is probably dead
                log("🔌 WebSocket state check failed - assuming closed")
                return

            # Check for forced turn end (NFC, etc) - even when not speaking
            import mute_button
            if mute_button.force_turn_end.is_set():
                if speaking:
                    log("🛑 Forced turn end event detected (NFC, etc) → ending turn.")
                    serial_com.write('L')
                    mute_button.force_turn_end.clear()
                    buf.clear(); globals()["LAST_MIC_METRICS"] = metrics
                    nfc.disable()
                    return
                else:
                    # Not speaking yet, but NFC wants to interrupt - exit to let agent respond
                    log("🛑 Forced turn end while waiting → exiting to let agent respond.")
                    mute_button.force_turn_end.clear()
                    nfc_triggered = True
                    break  # Exit loop but set flag first

            frames_read, chunk = mic.read()
            muted_now = is_muted()

            # Button PRESSED (unmuted) → Start sending after stabilization delay
            if (not speaking) and prev_muted and (not muted_now):
                log("🎙️ [PTT] Button pressed → stabilizing power rail...")
                # 250ms delay allows power rail to stabilize after GPIO change
                # before CPU ramp-up and mic activation
                await asyncio.sleep(0.15)
                log("🎙️ [PTT] Recording started")
                serial_com.write('U')  # Show unmuted/recording animation
                speaking = True
                set_idle(False)
                nfc.enable()

            # Button RELEASED (muted) → End turn immediately
            if speaking and (not prev_muted) and muted_now:
                log("🎙️ [PTT] Button released → ending turn")
                serial_com.write('L')  # Show loading animation while processing
                nfc.disable()
                # Send end of turn signal
                silent = b'\x00' * FRAME_BYTES
                for _ in range(END_SILENCE_CHUNKS):
                    await send_user_json(ws, {"user_audio_chunk": base64.b64encode(silent).decode()})
                    metrics.on_audio_sent(len(silent), voiced=None, synthetic=True)
                    await asyncio.sleep(FRAME_SEC)
                buf.clear(); globals()["LAST_MIC_METRICS"] = metrics
                return

            prev_muted = muted_now

            # If not speaking (button not pressed), just wait
            if not speaking:
                await asyncio.sleep(FRAME_SEC)
                continue

            # Speaking (button held): send all audio immediately, no VAD
            if frames_read <= 0:
                metrics.on_zero_len_read(); await asyncio.sleep(0); continue

            buf += chunk
            while len(buf) >= FRAME_BYTES:
                frame = bytes(buf[:FRAME_BYTES]); del buf[:FRAME_BYTES]
                try:
                    await send_user_json(ws, {"user_audio_chunk": base64.b64encode(frame).decode()})
                    metrics.on_audio_sent(len(frame), voiced=None)  # No VAD in PTT mode
                except Exception as e:
                    log(f"❌ Error sending audio frame: {e}")
                    raise

    except Exception as e:
        log(f"❌ Fatal error in stream_audio_ptt: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")
        raise
    finally:
        log("🧹 stream_audio_ptt cleanup...")
        safe_close(mic, "mic"); await post_close_grace()
        # Mark that this was NFC-triggered so session knows to get response
        if nfc_triggered:
            globals()["NFC_TRIGGERED_TURN"] = True
        log("✅ stream_audio_ptt cleanup complete")

async def stream_audio_vad(ws):
    """
    VAD Mode: Voice activity detection (hands-free).
    - Automatic speech detection
    - Silence detection ends turn
    - Button acts as simple mute toggle
    """
    mic = setup_mic(); metrics = TurnMetrics()
    preroll = deque(maxlen=PREROLL_FRAMES)
    buf = bytearray(); silence_chunks = speech_chunks = 0; speaking = False; gate_counter = 0

    # Track previous mute state for edge detection
    prev_muted = is_muted()

    # Mark idle while waiting for speech
    set_idle(True)
    log("🗣️ [VAD] Waiting for speech...")
    serial_com.write('U')  # Show unmuted/listening animation
    nfc.enable()
    
    try:
        while True:
            if STOP or get_state() != "running_agent":
                log("🛑 VAD audio streaming stopped (STOP or state changed)")
                return

            # Check if WebSocket is still alive
            try:
                if ws.protocol.state.name != "OPEN":
                    log("🔌 WebSocket closed while waiting for speech - exiting")
                    return
            except Exception:
                # If we can't check state, socket is probably dead
                log("🔌 WebSocket state check failed - assuming closed")
                return

            # If NFC or other event forces turn end
            import mute_button
            if mute_button.force_turn_end.is_set():
                log("🛑 Forced turn end event detected (NFC, etc) → injecting silence and ending turn.")
                serial_com.write('L')
                silent = b'\x00' * FRAME_BYTES
                for _ in range(END_SILENCE_CHUNKS):
                    await send_user_json(ws, {"user_audio_chunk": base64.b64encode(silent).decode()})
                    metrics.on_audio_sent(len(silent), voiced=None)
                    await asyncio.sleep(FRAME_SEC)
                mute_button.force_turn_end.clear()
                buf.clear(); globals()["LAST_MIC_METRICS"] = metrics
                nfc.disable()
                return

            frames_read, chunk = mic.read()
            muted_now = is_muted()

            # Update serial animation when mute state changes
            if muted_now != prev_muted:
                if muted_now:
                    serial_com.write('N')  # VAD muted animation
                else:
                    serial_com.write('U')  # VAD unmuted animation

            # If muted, pause recording but DON'T end turn
            if muted_now:
                # Just skip this frame - VAD will end turn via silence detection
                await asyncio.sleep(FRAME_SEC)
                continue

            prev_muted = muted_now

            if frames_read <= 0:
                metrics.on_zero_len_read(); await asyncio.sleep(0); continue

            buf += chunk
            while len(buf) >= FRAME_BYTES:
                frame = bytes(buf[:FRAME_BYTES]); del buf[:FRAME_BYTES]
                voiced = is_speech_exact(frame)

                if not speaking:
                    preroll.append(frame)
                    if voiced:
                        gate_counter += 1
                        if gate_counter >= START_GATE_FRAMES:
                            speaking = True
                            log("🗣️ [VAD] Speech detected → recording started")
                            nfc.enable()
                            set_idle(False)
                            # Send preroll buffer
                            while preroll:
                                f = preroll.popleft()
                                await send_user_json(ws, {"user_audio_chunk": base64.b64encode(f).decode()})
                                metrics.on_audio_sent(len(f), voiced=None)
                    else:
                        gate_counter = 0
                    continue

                # Speaking path: send frame
                await send_user_json(ws, {"user_audio_chunk": base64.b64encode(frame).decode()})
                metrics.on_audio_sent(len(frame), voiced=bool(voiced))

                if voiced:
                    speech_chunks += 1; silence_chunks = 0
                else:
                    silence_chunks += 1

                # VAD silence detection
                if speech_chunks >= MIN_CHUNKS and silence_chunks >= END_SILENCE_CHUNKS:
                    log("🤫 [VAD] Silence detected → ending turn")
                    serial_com.write('L')
                    nfc.disable()
                    buf.clear(); globals()["LAST_MIC_METRICS"] = metrics
                    return
    finally:
        safe_close(mic, "mic"); await post_close_grace()

# ==========================================================================
# SPEAKER TURN
# ==========================================================================

async def receive_response(ws, first_turn=False, barge_after_ms=0):
    out_buf = bytearray()  # Ensure out_buf is always defined, even if setup fails
    log("📥 Waiting for ElevenLabs response...") # AGENT TURN
    serial_com.write('L')
    # Agent is (potentially) about to speak; mark not idle
    set_idle(False)

    # --- Short-turn immediate skip ---
    lm = globals().get("LAST_MIC_METRICS", None)
    if lm and getattr(lm, "ms_sent", 0) < 800:
        log("⚡ Short user turn (<800ms) — skipping agent wait entirely.")
        serial_com.write('L')
        nfc.enable()
        return

    speaker = setup_speaker()
    metrics = TurnMetrics()

    text_chunks = []
    turn_start = time.time()
    last_content_at = turn_start
    saw_any_content = False
    saw_any_audio = False
    first_content_at = None
    last_transcript_at = None
    
    # Logging counters for diagnosis
    ws_msgs_received = 0
    last_log_at = time.time()
    LOG_INTERVAL = 5.0  # Log status every 5 seconds

    def drain(out_buf, pad_final=False):
        while len(out_buf) >= FRAME_BYTES:
            speaker.write(bytes(out_buf[:FRAME_BYTES]))
            del out_buf[:FRAME_BYTES]
        if pad_final and out_buf:
            pad = FRAME_BYTES - (len(out_buf) % FRAME_BYTES)
            speaker.write(out_buf + (b"\x00" * pad if pad < FRAME_BYTES else b""))
            out_buf.clear()

    async def grace_drain(out_buf):
        nonlocal last_content_at, saw_any_content, saw_any_audio, first_content_at, last_transcript_at
        deadline = time.time() + GRACE_DRAIN
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                drain(out_buf); continue
            except websockets.exceptions.ConnectionClosed:
                drain(out_buf, True); return False

            data = json.loads(raw); typ = data.get("type")
            if typ == "audio":
                b64 = (data.get("audio_event") or {}).get("audio_base_64", "")
                if b64:
                    if not saw_any_audio:
                        serial_com.write('O')   # 🔴 agent just started talking
                    pcm = base64.b64decode(b64); out_buf += pcm
                    metrics.on_agent_audio(len(pcm)); drain(out_buf)
                    saw_any_audio = saw_any_content = True
                    last_content_at = time.time()
                    if first_content_at is None: first_content_at = last_content_at
                    return True
            elif typ == "agent_response":
                txt = (data.get("agent_response_event") or {}).get("agent_response", "")
                if txt:
                    text_chunks.append(txt); metrics.on_agent_text(txt)
                    saw_any_content = True; last_content_at = time.time()
                    if first_content_at is None: first_content_at = last_content_at
                    return True
            elif typ == "user_transcript":
                ut = (data.get("user_transcription_event") or {}).get("user_transcript", "")
                if ut:
                    metrics.on_user_transcript(); last_transcript_at = time.time()
                    log(f"👤 [User transcript]: {ut}")
            drain(out_buf)
        return False

    try:
        while True:
            if STOP:
                return
            now = time.time()
            
            # Periodic status logging for debugging hangs
            if now - last_log_at >= LOG_INTERVAL:
                elapsed = now - turn_start
                log(f"📊 [receive_response] Status: elapsed={elapsed:.1f}s, msgs={ws_msgs_received}, "
                    f"saw_content={saw_any_content}, saw_audio={saw_any_audio}, text_chunks={len(text_chunks)}")
                last_log_at = now
        
            if not saw_any_content:
                lm = globals().get("LAST_MIC_METRICS", None)
                elapsed = now - turn_start
                # Dynamically adjust maximum wait based on user audio duration
                adaptive_max = FIRST_CONTENT_MAX
                if lm and getattr(lm, "ms_sent", 0) < 800:
                    adaptive_max = min(1.0, FIRST_CONTENT_MAX)  # cap timeout a>
            
                if elapsed > adaptive_max:
                    if await grace_drain(out_buf):
                        continue
                    full_text = "".join(text_chunks).strip()
                    if not full_text:
                        serial_com.write('L')
                        nfc.enable()
                        return
                    log(f"🧠 [Agent full]: {full_text or '(no text)'}")
                    serial_com.write('L')
                    drain(out_buf, True)
                    nfc.enable()
                    return
            else:
                if first_turn and first_content_at is not None and barge_after_ms > 0:
                    if (now - first_content_at) * 1000.0 >= barge_after_ms:
                        drain(out_buf)
                        partial = "".join(text_chunks).strip()
                        if partial:
                            log(f"🧠 [Agent partial]: {partial}")
                        log("✅ First-turn barge: opening mic early.")
                        serial_com.write('M')
                        return
                if (now - last_content_at) > CONTENT_IDLE:
                    if await grace_drain(out_buf):
                        continue
                    drain(out_buf, True)
                    full_text = "".join(text_chunks).strip()
                    if not full_text:
                        serial_com.write('L')
                        return
                    log(f"🧠 [Agent full]: {full_text or '(no text)'}")
                    return  # END OF AGENT TURN

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.25)
                ws_msgs_received += 1
            except asyncio.TimeoutError:
                drain(out_buf); continue
            except websockets.exceptions.ConnectionClosed:
                log("🔌 WebSocket closed during receive_response")
                drain(out_buf, True); raise

            data = json.loads(raw); typ = data.get("type")
            
            # Log all non-ping message types for debugging
            if typ != "ping":
                if detail: log(f"📨 [WS] Received: {typ}")
            if typ == "audio":
                b64 = (data.get("audio_event") or {}).get("audio_base_64", "")
                if b64:
                    if not saw_any_audio:
                        serial_com.write('O')   # 🔴 agent just started talking
                    pcm = base64.b64decode(b64); out_buf += pcm
                    metrics.on_agent_audio(len(pcm)); drain(out_buf)
                    saw_any_audio = saw_any_content = True
                    last_content_at = time.time()
                    if first_content_at is None: first_content_at = last_content_at
            elif typ == "agent_response":
                txt = (data.get("agent_response_event") or {}).get("agent_response", "")
                if txt:
                    text_chunks.append(txt); metrics.on_agent_text(txt)
                    saw_any_content = True; last_content_at = time.time()
                    if first_content_at is None: first_content_at = last_content_at
            elif typ == "user_transcript":
                ut = (data.get("user_transcription_event") or {}).get("user_transcript", "")
                if ut:
                    metrics.on_user_transcript(); last_transcript_at = time.time()
                    log(f"👤 [User transcript]: {ut}")
            elif typ == "ping":
                event = data.get("ping_event", {})
                event_id = event.get("event_id")
                ping_ms = event.get("ping_ms") or 0
                asyncio.create_task(send_pong(ws, event_id, ping_ms))
                #log(f"🏓 ElevenLabs ping received (event_id={event_id}) — scheduling pong")
            elif typ in ("user_activity_ack", "server_activity_ack"):
                log(f"🟢 ElevenLabs keepalive ACK received: {typ}")
            #else:
                #log(f"[WS DEBUG RAW] {typ}: {data}")
    finally:
        safe_close(speaker, "speaker"); await post_close_grace()
        # Back to idle after speaker is done
        set_idle(True)

# ==========================================================================
# SESSION LOOP
# ==========================================================================

async def run_session():
    headers=[("xi-api-key",API_KEY)]
    backoff=1.0
    did_init=False
    
    # Build TTS config - add volume parameter only for Nova agent
    tts_config = {"output_audio_format":"pcm_16000"}
    if AGENT_ID == "agent_1701k5bgdzmte5f9q518mge3jsf0":
        tts_config["volume"] = 5  # Increase volume by 50% for Nova
        log("🔊 Nova agent detected - setting volume to 1.5x")
    
    INIT_MSG={"type":"conversation_initiation_client_data",
        "conversation_config_override":{"tts":tts_config,"asr":{"input_audio_format":"pcm_16000"}}}
    SUPPRESS_GREETING={"type":"conversation_initiation_client_data",
        "conversation_config_override":{"agent":{"first_message":""},"tts":tts_config,"asr":{"input_audio_format":"pcm_16000"}}}

    try:
        while True:
            # Check if we should still be running
            if STOP or get_state() != "running_agent":
                log("🛑 Exiting session loop (STOP or state changed).")
                serial_com.write('L')
                return
            try:
                async with websockets.connect(
                    WS_ENDPOINT + "&inactivity_timeout=600",
                    additional_headers=headers,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                    max_size=10_000_000
                ) as ws:
                    log("🔗 Connected to ElevenLabs agent WebSocket.")
                    backoff=1.0

                    nfc.set_sender(ws, asyncio.get_event_loop())

                    # Start background pong listener (idle keepalive)
                    global pong_task
                    pong_task = asyncio.create_task(maintain_pong(ws))

                    if not did_init:
                        try:
                            # AGENT start routine, the agent talks first
                            await ws.send(json.dumps(INIT_MSG)) # AGENT start routine
                            await send_user_json(ws, {"type":"user_activity"}) # AGENT start routine
                            # Stop idle keepalive while receiving full stream
                            pong_task.cancel()
                            try:
                                await pong_task
                            except asyncio.CancelledError:
                                pass

                            await receive_response(ws, first_turn=True, barge_after_ms=FIRST_TURN_BARGE_AFTER_MS)
                            did_init=True

                            # Restart idle keepalive
                            pong_task = asyncio.create_task(maintain_pong(ws))
                        except Exception:
                            did_init=True
                    else:
                        try:
                            await ws.send(json.dumps(SUPPRESS_GREETING))
                        except Exception:
                            pass

                    while True:
                        try:
                            # Start streaming audio (user turn)
                            await stream_audio(ws)
                            log("✅ stream_audio() returned")
                            
                            # Check if state changed during audio streaming
                            if get_state() != "running_agent":
                                log("🛑 State changed during audio streaming - exiting session")
                                return
                            
                            # Check if NFC triggered this turn (skip short-turn logic)
                            nfc_triggered = globals().pop("NFC_TRIGGERED_TURN", False)
                            if nfc_triggered:
                                log("📨 NFC-triggered turn → proceeding to agent response")
                                # Skip short-turn check, always get agent response
                            else:
                                log("🔍 Checking for short turn...")
                                lm = globals().get("LAST_MIC_METRICS", None)
                                if lm:
                                    effective_ms = getattr(lm, "ms_sent", 0) - getattr(lm, "synthetic_ms_sent", 0)
                                    if effective_ms < 800:
                                        log("⚡ Short (effective) user turn — skipping agent response phase entirely.")
                                        try:
                                            log("🔄 Cancelling pong_task for short turn...")
                                            pong_task.cancel()
                                            await pong_task
                                            log("✅ pong_task cancelled")
                                        except Exception as e:
                                            log(f"⚠️ pong_task cancel error: {e}")
                                        serial_com.write('L')
                                        nfc.enable()
                                        pong_task = asyncio.create_task(maintain_pong(ws))
                                        await asyncio.sleep(0.15)
                                        continue
                                else:
                                    log("📏 Normal turn (no short-turn skip)")
                    
                            # Pause idle keepalive before recv() to avoid collision
                            log("🔄 Cancelling pong_task before receive_response...")
                            pong_task.cancel()
                            try:
                                # Add timeout to prevent infinite hang
                                await asyncio.wait_for(pong_task, timeout=2.0)
                                log("✅ pong_task cancelled successfully")
                            except asyncio.TimeoutError:
                                log("⏰ pong_task cancellation timed out after 2s - forcing continue")
                                # Don't wait, just proceed - pong_task will die with websocket
                            except asyncio.CancelledError:
                                log("✅ pong_task cancelled (CancelledError)")
                            except Exception as e:
                                log(f"⚠️ pong_task await error: {e}")
                    
                            # Now listen for agent response (recv owns socket)
                            log("▶️  Calling receive_response()...")
                            await receive_response(ws)
                    
                            # Resume idle keepalive between turns
                            pong_task = asyncio.create_task(maintain_pong(ws))
                    
                            await asyncio.sleep(0.15)
                        except websockets.exceptions.ConnectionClosed as e:
                            log(f"🔌 WS closed by server: code={getattr(e, 'code', '?')}, reason={getattr(e, 'reason', '?')}")
                            raise
                        except Exception as e:
                            log(f"❌ Turn error: {e}")
                            raise
            except Exception as e:
                log(f"↻ Will reconnect after error: {e}")
            finally:
                # Stop keepalive on disconnect
                try:
                    pong_task.cancel()
                    await pong_task
                except Exception:
                    pass

            sleep_s = min(backoff, 10.0) + random.uniform(0, 0.25)
            log(f"⏳ Reconnecting in {sleep_s:.2f}s ...")
            serial_com.write('L')
            await asyncio.sleep(sleep_s)
            backoff = min(backoff * 2, 10.0)
    finally:
        pass
# ==========================================================================
# NFC INITIALIZATION
# ==========================================================================

# Initialize NFC reader with callback (after on_nfc_tag_detected is defined)
nfc = nfc_backend.NfcReader(
    agent_id=AGENT_ID,
    base_dir="/home/orb/AIflow",
    debounce_s=1.5,
    log=print,
    tags_url=NFC_TAGS_URL,
    tag_callback=on_nfc_tag_detected
)
nfc.start()

# ==========================================================================
# MAIN CONTROL LOOP
# ==========================================================================

async def main_control_loop():
    """
    Main application state machine loop.
    Controls transitions between splash_idle and running_agent states.
    """
    # Store event loop reference for thread-safe async task scheduling
    global MAIN_EVENT_LOOP
    MAIN_EVENT_LOOP = asyncio.get_event_loop()

    log("🚀 Application starting...")

    # Play startup test audio to confirm audio system is working
    play_startup_test_audio()

    log("🟢 Entering splash_idle — waiting for NFC tag (TEST or AGENT_START)...")
    serial_com.write('S')  # Splash idle - waiting for NFC scan

    while True:
        if STOP:
            log("🛑 Stop flag set, exiting main loop.")
            return

        current_state = get_state()

        if current_state == "splash_idle":
            # Wait in idle state for NFC interaction
            await asyncio.sleep(0.25)

        elif current_state == "running_agent":
            # AGENT_START tag was scanned - start conversation session
            log("🤖 Starting agent conversation session...")
            try:
                await run_session()
            except Exception as e:
                log(f"❌ Agent session error: {e}")
                # Stay in running_agent state to allow auto-reconnect
                await asyncio.sleep(1.0)

        else:
            # Unknown state - reset to idle
            log(f"⚠️ Unknown state: {current_state}, resetting to splash_idle")
            set_state("splash_idle")

# ==========================================================================
# SIGNAL HANDLERS
# ==========================================================================

def _shutdown(loop):
    serial_com.write('B')
    global STOP
    STOP = True
    safe_close_all()
    stop_mute_button()
    nfc.stop()
    try:
        pass
    except Exception:
        pass
    try:
        if 'pong_task' in globals() and pong_task is not None:
            pong_task.cancel()
            log("🔌 Pong loop cancelled during shutdown.")
    except Exception as e:
        log(f"⚠️ Failed to cancel pong loop: {e}")

    for task in asyncio.all_tasks(loop):
        task.cancel()

if __name__=="__main__":
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,lambda:_shutdown(loop))
    try:
        loop.run_until_complete(main_control_loop())
    except KeyboardInterrupt:
        log("⚠️ Keyboard interrupt detected")
        serial_com.write('B')
    finally:
        serial_com.write('B')  # Ensure 'B' is sent on any exit
        safe_close_all()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()