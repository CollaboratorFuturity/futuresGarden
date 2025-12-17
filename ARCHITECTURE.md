# The Orb - System Architecture

**Version:** v1.0.7
**Last Updated:** 2025-01-15

---

## Table of Contents

1. [Architecture Philosophy](#architecture-philosophy)
2. [System Layers](#system-layers)
3. [Process Model](#process-model)
4. [Threading Model](#threading-model)
5. [State Machine](#state-machine)
6. [Audio Pipeline](#audio-pipeline)
7. [WebSocket Communication](#websocket-communication)
8. [Race Condition Mitigation](#race-condition-mitigation)
9. [Memory Management](#memory-management)
10. [Power Management](#power-management)
11. [Filesystem Strategy](#filesystem-strategy)
12. [Error Handling & Recovery](#error-handling--recovery)
13. [Performance Characteristics](#performance-characteristics)
14. [Design Decisions](#design-decisions)
15. [Known Limitations](#known-limitations)
16. [Future Enhancements](#future-enhancements)

---

## Architecture Philosophy

The Orb system is designed around the following core principles:

### 1. **Separation of Concerns**

Three independent processes with minimal coupling:
- **battery_log.py**: Power monitoring (critical for hardware safety)
- **config_fetcher.py**: Startup orchestration (ephemeral, exits after handoff)
- **main.py**: Application runtime (persistent, user-facing)

This separation ensures:
- Battery monitoring never affected by application crashes
- OTA updates isolated from runtime state
- Clear failure domains

### 2. **Defensive Programming**

Assumptions about external systems:
- Network can fail at any time
- WebSocket connections will drop
- Hardware sensors can return invalid data
- Audio devices may become unavailable
- NFC tags may be corrupted

Mitigations:
- Retry logic with exponential backoff
- Graceful degradation (continue without telemetry if upload fails)
- Timeout protection on all blocking operations
- Validation of all external data

### 3. **Zero-Downtime Operations**

Process replacement via `os.execv()` enables:
- OTA updates without user-visible interruption
- Configuration changes without restart
- Memory leak recovery (future: automatic restart on threshold)

### 4. **Embedded Constraints**

Raspberry Pi Zero W 1.1 limitations:
- **CPU**: 1GHz single-core ARM11 (very modest performance)
- **RAM**: 512MB (aggressive management required)
- **Storage**: microSD (slow I/O, limited write cycles)
- **Power**: Battery-powered (efficiency critical, lower consumption than Zero 2 W)

Architectural choices influenced by constraints:
- Minimal dependencies (no heavyweight frameworks)
- Direct hardware access (ALSA, GPIO, I2C) instead of abstractions
- tmpfs for volatile data (reduce SD writes)
- Read-only filesystem for production (extend SD lifespan)

### 5. **Fail-Safe Behavior**

Critical path: Prevent battery damage and data corruption
- Under-voltage detection triggers immediate shutdown
- Filesystem remount logic with fallback to read-only
- Backup/restore for OTA updates
- Display 'D' animation locked during critical shutdown

---

## System Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│  - main.py (Voice agent logic)                              │
│  - WebSocket conversation management                         │
│  - Turn-taking coordination                                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                  Audio Processing Layer                      │
│  - ALSA capture/playback                                     │
│  - WebRTC VAD (voice activity detection)                     │
│  - Frame buffering and streaming                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    Hardware Abstraction Layer                │
│  - serial_com.py (display)                                   │
│  - mute_button.py (GPIO)                                     │
│  - nfc_backend.py (I2C PN532)                                │
│  - INA219.py (I2C battery sensor)                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      Kernel Interfaces                       │
│  - ALSA (libasound.so)                                       │
│  - GPIO (/sys/class/gpio or gpiod)                           │
│  - I2C (/dev/i2c-1 via smbus)                                │
│  - Serial (/dev/ttyUSB0 via termios)                         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                     Hardware Layer                           │
│  - BCM2835 SoC (ARM11 CPU, GPIO, I2C, UART)                  │
│  - USB Audio (microphone + speaker)                          │
│  - PN532 NFC reader                                          │
│  - INA219 power monitor                                      │
│  - LiPo battery (2000 mAh)                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Process Model

### Boot Sequence

```
┌──────────────────────────────────────────────────────────────┐
│                      System Boot                              │
│  - Kernel initialization                                      │
│  - systemd starts                                             │
└──────────────────────────────────────────────────────────────┘
                            ↓
          ┌─────────────────┴─────────────────┐
          │                                   │
          ▼                                   ▼
┌────────────────────┐          ┌────────────────────────────┐
│ battery_log.service│          │  config_fetcher.service    │
│                    │          │  After=network-online      │
│  Starts immediate  │          │  Wants=network-online      │
│  (no dependencies) │          └────────────────────────────┘
└────────────────────┘                      ↓
          │                    ┌────────────────────────────┐
          │                    │  Wait for network (60s)    │
          │                    └────────────────────────────┘
          │                                ↓
          │                    ┌────────────────────────────┐
          │                    │  Fetch config from API     │
          │                    │  Check for OTA updates     │
          │                    │  Apply WiFi/volume         │
          │                    │  Write /tmp/aiflow.env     │
          │                    └────────────────────────────┘
          │                                ↓
          │                    ┌────────────────────────────┐
          │                    │  os.execv() → main.py      │
          │                    │  (Same PID, new process)   │
          │                    └────────────────────────────┘
          │                                ↓
          │                    ┌────────────────────────────┐
          │                    │  main.py runtime           │
          │                    │  - Load .env               │
          │                    │  - Init hardware           │
          │                    │  - Enter state machine     │
          │                    └────────────────────────────┘
          │                                │
          └────────────────────────────────┘
                   (Both running)
```

### Process Lifecycle

#### battery_log.py (PID 1234)

```
Lifecycle: Persistent (systemd restart on failure)

Start
  ↓
Initialize INA219 sensor (I2C 0x43)
  ↓
[Main Loop - Runs until shutdown]
  │
  ├─ [Every 30s] Read voltage/current
  │   ├─ Dual-average readings (50ms apart)
  │   ├─ Calculate battery percentage
  │   ├─ Get system health (temp, memory, throttling)
  │   ├─ Check under-voltage flag
  │   ├─ [If under-voltage] → IMMEDIATE SHUTDOWN
  │   ├─ [If critical voltage] → GRACEFUL SHUTDOWN
  │   ├─ [If low voltage] → Display warning
  │   └─ Queue telemetry for upload
  │
  └─ [Every 90s] Process upload queue
      ├─ Batch pending telemetry
      ├─ HTTP POST to Supabase
      ├─ [On success] Clear queue
      └─ [On failure] Retry (3 attempts, 5s timeout)

Shutdown
  ↓
Display 'D' animation (dead/shutdown)
  ↓
sudo poweroff
```

**Independence Rationale:**
- Battery monitoring MUST NOT depend on main application state
- If main.py crashes or hangs, battery protection remains active
- Separate PID prevents resource contention with audio processing

#### config_fetcher.py (PID 5678) → main.py (Same PID 5678)

```
Lifecycle: Ephemeral (exits after handoff to main.py)

Start (as config_fetcher.service)
  ↓
Wait for network (poll 1.1.1.1, 60s timeout)
  ↓
Fetch device config from Supabase API
  ↓
[OTA Update Check]
  ├─ GET GitHub releases API
  ├─ Compare versions
  ├─ [If newer] Download + validate + install
  └─ [If success] Update version file
  ↓
Apply WiFi credentials (nmcli)
  ↓
Set system volume (amixer)
  ↓
Write /tmp/aiflow.env
  ↓
os.execv("/home/orb/env/bin/python", ["/home/orb/AIflow/main.py"])
  │
  └─→ Process image replaced (PID unchanged)
        ↓
      main.py runtime
        │
        ├─ Load .env from /tmp/aiflow.env
        ├─ Initialize hardware (GPIO, I2C, serial)
        ├─ Play startup test audio
        ├─ Enter state machine (splash_idle)
        │
        └─ [Main event loop - Runs until SIGTERM/SIGINT]
            │
            ├─ splash_idle: Wait for NFC tag
            │   └─ [On AGENT_START tag] → running_agent
            │
            └─ running_agent: Conversation loop
                ├─ Connect WebSocket
                ├─ Start pong_task
                ├─ [Loop] User turn → Agent turn → Repeat
                └─ [On error/signal] Graceful shutdown
```

**Process Replacement Rationale:**
- Preserve PID for systemd supervision
- Zero-downtime updates (no service restart)
- Clean memory slate (old process image discarded)
- Inherit file descriptors, environment, working directory

---

## Threading Model

### main.py Thread Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Main Thread                             │
│                    (asyncio event loop)                      │
│                                                              │
│  - run_session() WebSocket loop                              │
│  - stream_audio() audio capture + send                       │
│  - receive_response() audio receive + playback               │
│  - maintain_pong() background keepalive                      │
│  - NFC phrase injection (via run_coroutine_threadsafe)       │
│                                                              │
│  asyncio Tasks:                                              │
│  ├─ pong_task (background, cancellable)                      │
│  └─ Main conversation loop (sequential turns)                │
└─────────────────────────────────────────────────────────────┘
                            ↕
                     (Shared Resources)
                            ↕
┌──────────────────────────┬──────────────────────────────────┐
│    Button Thread         │       NFC Thread                  │
│  (mute_button.py)        │     (nfc_backend.py)              │
│                          │                                   │
│  while running:          │  while running:                   │
│    state = GPIO.input()  │    tag_uid = pn532.read()        │
│    if edge_detected():   │    if tag_uid and not_debounced():│
│      update_mute_state() │      phrase = lookup(tag_uid)    │
│      trigger_callbacks() │      queue_phrase(phrase)        │
│    sleep(0.01)  # 10ms   │      if ws: inject_async()       │
│                          │    sleep(0.2)  # 200ms timeout   │
└──────────────────────────┴──────────────────────────────────┘
                            ↕
                     (Synchronization)
                            ↕
┌─────────────────────────────────────────────────────────────┐
│                  Shared State & Locks                        │
│                                                              │
│  Global Variables (main.py):                                 │
│  - STATE: str (splash_idle, running_agent)                   │
│  - STATE_LOCK: threading.Lock                                │
│  - STOP: bool (shutdown flag)                                │
│  - LAST_MIC_METRICS: TurnMetrics                             │
│  - NFC_TRIGGERED_TURN: bool                                  │
│                                                              │
│  mute_button.py:                                             │
│  - _muted: threading.Event (mute state)                      │
│  - _force_turn_end: threading.Event (NFC interrupt)          │
│  - _state_check: callable (state validation)                 │
│                                                              │
│  nfc_backend.py:                                             │
│  - _ws: WebSocket (for phrase injection)                     │
│  - _loop: asyncio.AbstractEventLoop                          │
│  - _phrase_queue: deque(maxlen=16)                           │
│  - _enabled: bool (scanning control)                         │
└─────────────────────────────────────────────────────────────┘
```

### Thread Synchronization

#### Main Thread ↔ Button Thread

```python
# Button thread updates mute state
def _button_loop():
    while _running:
        current = not GPIO.input(_pin)  # Active-low

        if current != _prev_state:
            _debounce_start = time.time()
            _prev_state = current

        if (time.time() - _debounce_start) >= _debounce_s:
            if _mode == "PTT":
                # Momentary: immediate update
                if current:
                    _muted.clear()  # Unmuted
                    _set_idle(False)  # Main thread callback
                else:
                    _muted.set()  # Muted
                    _set_idle(True)
            else:  # VAD
                # Toggle: only on press (not release)
                if current and not _last_processed:
                    if _muted.is_set():
                        _muted.clear()
                    else:
                        _muted.set()
                    _last_processed = True
                elif not current:
                    _last_processed = False

        time.sleep(_poll_s)  # 10ms

# Main thread checks mute state
def is_muted():
    return _muted.is_set()  # Thread-safe (Event.is_set() is atomic)
```

#### Main Thread ↔ NFC Thread

```python
# NFC thread detects tag and injects phrase
def _nfc_loop():
    while _running:
        if not _enabled:
            time.sleep(0.2)
            continue

        try:
            uid = pn532.read_passive_target(timeout=0.2)
            if uid:
                uid_str = format_uid(uid)

                # Debounce check
                if uid_str == _last_uid and (time.time() - _last_read_time) < _debounce_s:
                    continue

                _last_uid = uid_str
                _last_read_time = time.time()

                phrase = _tags.get(uid_str)
                if phrase:
                    if phrase in ["TEST", "AGENT_START"]:
                        # Synchronous callback to main thread
                        _tag_callback(phrase)
                    else:
                        # Async phrase injection
                        if _ws and _loop:
                            future = asyncio.run_coroutine_threadsafe(
                                _ws.send(json.dumps({
                                    "type": "user_message",
                                    "text": phrase
                                })),
                                _loop
                            )
                            future.result(timeout=2.0)  # Block NFC thread until sent
        except Exception as e:
            _log(f"NFC error: {e}")
            time.sleep(1.0)

# Main thread enables/disables scanning
def enable():
    global _enabled
    _enabled = True

def disable():
    global _enabled
    _enabled = False
```

### Thread Safety Analysis

| Resource | Access Pattern | Synchronization Mechanism |
|----------|----------------|---------------------------|
| **STATE** (main.py) | Main: R/W, Button: R (via callback) | `STATE_LOCK` (threading.Lock) |
| **STOP** (main.py) | Main: W, All: R | Simple bool (Python GIL ensures atomic read/write) |
| **_muted** (mute_button.py) | Button: W, Main: R | `threading.Event` (atomic) |
| **_force_turn_end** (mute_button.py) | Button/NFC: W, Main: R/Clear | `threading.Event` (atomic) |
| **_ws, _loop** (nfc_backend.py) | Main: W (once), NFC: R | Set once at startup, never modified |
| **_enabled** (nfc_backend.py) | Main: W, NFC: R | Simple bool (GIL protects, low-frequency writes) |
| **ALSA devices** | Main only | No sharing (button/NFC never touch audio) |
| **Serial port** (serial_com.py) | All threads | `_lock` (threading.Lock) in serial_com module |

**GIL Consideration:**
- Python's Global Interpreter Lock (GIL) serializes bytecode execution
- Simple variable reads/writes are atomic (bool, int, str assignments)
- Collections (dict, list) are NOT thread-safe without locks
- `threading.Event`, `threading.Lock` provide explicit synchronization

**Deadlock Prevention:**
- Locks never nested (single-lock acquisitions only)
- Button/NFC callbacks are non-blocking (don't acquire Main thread locks)
- `run_coroutine_threadsafe()` uses internal queue (no direct lock)

---

## State Machine

### States

```
┌─────────────────────────────────────────────────────────────┐
│                       splash_idle                            │
│                                                              │
│  - Display: 'S' (splash screen)                              │
│  - Button: Inactive (state_check returns False)              │
│  - NFC: Active (scanning enabled)                            │
│  - WebSocket: Disconnected                                   │
│  - Waiting for: NFC "AGENT_START" tag or "TEST" tag          │
└─────────────────────────────────────────────────────────────┘
                   │                       ↑
                   │ AGENT_START tag       │ End session
                   │ detected              │ (error, signal)
                   ↓                       │
┌─────────────────────────────────────────────────────────────┐
│                      running_agent                           │
│                                                              │
│  - Display: Dynamic ('U', 'M', 'L', 'O')                     │
│  - Button: Active (state_check returns True)                 │
│  - NFC: Active (for phrase injection)                        │
│  - WebSocket: Connected to ElevenLabs                        │
│  - Conversation loop: User turn ↔ Agent turn                 │
└─────────────────────────────────────────────────────────────┘
```

### State Transitions

```
[BOOT]
  ↓
main.py starts
  ↓
set_state("splash_idle")
  ↓
┌──────────────────────────────────────┐
│         splash_idle                  │
│                                      │
│  Loop:                               │
│    - Display 'S'                     │
│    - Wait for NFC tag                │
│    - Check STOP flag                 │
└──────────────────────────────────────┘
        │           │
        │           │ TEST tag detected
        │           └──→ hot_reload_config()
        │                   └─→ Stay in splash_idle
        │
        │ AGENT_START tag detected
        ↓
on_nfc_tag_detected("AGENT_START")
        ↓
set_state("running_agent")
        ↓
┌──────────────────────────────────────┐
│        running_agent                 │
│                                      │
│  run_session() called                │
│    ├─ Connect WebSocket              │
│    ├─ Start pong_task                │
│    ├─ Play greeting (first turn)     │
│    └─ Loop:                          │
│        ├─ stream_audio()             │
│        │   └─ Cancel pong_task       │
│        ├─ receive_response()         │
│        └─ Restart pong_task          │
└──────────────────────────────────────┘
        │
        │ Error, WebSocket close, or SIGTERM
        ↓
Cleanup:
  - Close WebSocket
  - Close audio devices
  - Stop button/NFC threads
  - Display 'B' (bye)
        ↓
set_state("splash_idle")
        ↓
Return to splash_idle loop
```

### State Checks

```python
# Button handler state check (mute_button.py)
def _button_loop():
    while _running:
        # ...
        if _state_check and not _state_check():
            # Not in running_agent state, ignore button
            time.sleep(_poll_s)
            continue

        # Process button normally
        # ...

# Set by main.py
mute_button.set_state_check(lambda: get_state() == "running_agent")
```

This prevents button presses from affecting the system while in `splash_idle` state.

---

## Audio Pipeline

### Capture Pipeline (Microphone → ElevenLabs)

```
┌──────────────────────────────────────────────────────────────┐
│                    Physical Microphone                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
                  (Analog audio signal)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    USB Audio Interface                        │
│  - ADC (Analog-to-Digital Converter)                          │
│  - Sample rate: 16kHz                                         │
│  - Format: 16-bit signed PCM                                  │
└──────────────────────────────────────────────────────────────┘
                            ↓
                    (Digital PCM samples)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                      ALSA Driver                              │
│  - Kernel buffer (ring buffer)                                │
│  - Period size: 480 samples (30ms @ 16kHz)                    │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                  pyalsaaudio (Python binding)                 │
│  - alsaaudio.PCM_CAPTURE                                      │
│  - pcm.read() → (num_frames, bytes_data)                      │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│              main.py: stream_audio_ptt/vad()                  │
│                                                              │
│  frames_read, chunk = mic.read()                             │
│  # chunk = 960 bytes (480 samples × 2 bytes)                 │
└──────────────────────────────────────────────────────────────┘
                            ↓
                  [VAD Mode Only]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    webrtcvad.Vad                              │
│  - is_speech(chunk, sample_rate=16000)                        │
│  - Returns: True (speech) or False (silence)                  │
│  - Mode 3 (most aggressive)                                   │
└──────────────────────────────────────────────────────────────┘
                            ↓
              [If voiced OR PTT mode]
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    Base64 Encoding                            │
│  - base64.b64encode(chunk).decode()                           │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    JSON Serialization                         │
│  - {"user_audio_chunk": base64_string}                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    WebSocket Send                             │
│  - await ws.send(json.dumps(msg))                             │
│  - TLS encrypted (WSS)                                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
                     (Network transmission)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                  ElevenLabs API Server                        │
│  - Receive audio chunks                                       │
│  - Perform ASR (Automatic Speech Recognition)                 │
│  - Generate agent response                                    │
└──────────────────────────────────────────────────────────────┘
```

### Playback Pipeline (ElevenLabs → Speaker)

```
┌──────────────────────────────────────────────────────────────┐
│                  ElevenLabs API Server                        │
│  - Generate TTS (Text-to-Speech)                              │
│  - Stream audio chunks (base64-encoded PCM)                   │
└──────────────────────────────────────────────────────────────┘
                            ↓
                     (Network transmission)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    WebSocket Receive                          │
│  - raw = await ws.recv()                                      │
│  - Timeout: 0.1s (non-blocking)                               │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    JSON Parsing                               │
│  - data = json.loads(raw)                                     │
│  - typ = data.get("type")                                     │
│  - [If typ == "audio"]                                        │
│    b64 = data["audio_event"]["audio_base_64"]                 │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    Base64 Decoding                            │
│  - pcm = base64.b64decode(b64)                                │
│  - Chunk size varies (typically 1-10KB)                       │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    Output Buffer                              │
│  - out_buf += pcm  (bytearray append)                         │
│  - Accumulates until full frame ready                         │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    drain(out_buf)                             │
│                                                              │
│  while len(out_buf) >= FRAME_BYTES:  # 960 bytes             │
│      speaker.write(bytes(out_buf[:FRAME_BYTES]))             │
│      del out_buf[:FRAME_BYTES]                               │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                  pyalsaaudio (Python binding)                 │
│  - alsaaudio.PCM_PLAYBACK                                     │
│  - pcm.write(frame_bytes)                                     │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                      ALSA Driver                              │
│  - Kernel buffer (ring buffer)                                │
│  - Period size: 480 samples (30ms @ 16kHz)                    │
│  - Blocks if buffer full (backpressure)                       │
└──────────────────────────────────────────────────────────────┘
                            ↓
                    (Digital PCM samples)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                    USB Audio Interface                        │
│  - DAC (Digital-to-Analog Converter)                          │
│  - Sample rate: 16kHz                                         │
│  - Format: 16-bit signed PCM                                  │
└──────────────────────────────────────────────────────────────┘
                            ↓
                  (Analog audio signal)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│                     Physical Speaker                          │
└──────────────────────────────────────────────────────────────┘
```

### Buffering Strategy

```
Audio Chunk Arrival Pattern (ElevenLabs → Device):

Time:  0ms    100ms   200ms   300ms   400ms   500ms
       │      │       │       │       │       │
Chunks: ▓▓▓▓▓  ▓▓     ▓▓▓▓    ▓       ▓▓▓▓▓   ▓▓▓
        ↓
      out_buf (bytearray)
        │
        ├─ Append incoming chunks immediately
        ├─ drain() called after each append
        └─ Write full frames (960 bytes) to speaker

drain() Logic:

    while len(out_buf) >= FRAME_BYTES:
        speaker.write(out_buf[:FRAME_BYTES])
        del out_buf[:FRAME_BYTES]

Result:
  - Partial frames remain in buffer
  - Next chunk arrival completes frame
  - Smooth playback (no gaps)

Example:

  out_buf = bytearray()

  # Chunk 1 arrives: 2400 bytes
  out_buf += chunk1  # len=2400
  drain(out_buf)
    # Write frame 1 (960 bytes), remaining: 1440
    # Write frame 2 (960 bytes), remaining: 480

  # Chunk 2 arrives: 800 bytes
  out_buf += chunk2  # len=1280
  drain(out_buf)
    # Write frame 3 (960 bytes), remaining: 320

  # Chunk 3 arrives: 1200 bytes
  out_buf += chunk3  # len=1520
  drain(out_buf)
    # Write frame 4 (960 bytes), remaining: 560
```

---

## WebSocket Communication

### Connection Lifecycle

```
[main.py: run_session() called]
  ↓
Connect WebSocket
  ↓
┌──────────────────────────────────────────────────────────────┐
│  ws = await websockets.connect(                              │
│      WS_ENDPOINT,                                            │
│      extra_headers={"xi-api-key": API_KEY},                  │
│      ping_interval=None  # Manual ping/pong                  │
│  )                                                           │
└──────────────────────────────────────────────────────────────┘
  ↓
[Connection established]
  ↓
Start pong_task (maintain_pong)
  ↓
┌──────────────────────────────────────────────────────────────┐
│                    Conversation Loop                          │
│                                                              │
│  while True:                                                 │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ User Turn: stream_audio(ws, pong_task)             │  │
│    │   ├─ Cancel pong_task when speaking starts         │  │
│    │   ├─ Send audio chunks                             │  │
│    │   └─ Return when turn ends                         │  │
│    └─────────────────────────────────────────────────────┘  │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ Agent Turn: receive_response(ws)                   │  │
│    │   ├─ Receive audio + text messages                 │  │
│    │   ├─ Play audio to speaker                         │  │
│    │   └─ Return when response complete                 │  │
│    └─────────────────────────────────────────────────────┘  │
│    ┌─────────────────────────────────────────────────────┐  │
│    │ Restart pong_task for idle period                  │  │
│    └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
  │
  │ [Error, WebSocket close, or STOP flag]
  ↓
Cleanup:
  - Cancel pong_task
  - Close WebSocket
  - Close audio devices
  ↓
Return to state machine
```

### Message Flow Diagram

```
Client (main.py)                        ElevenLabs Server
     │                                          │
     │────── WebSocket Connect ───────────────→│
     │                                          │
     │←────── Connection Established ──────────│
     │                                          │
     │                                          │
     ├─ Start pong_task (background)           │
     │   (Periodically sends pong responses)   │
     │                                          │
     │                                          │
     │────── user_audio_chunk (base64) ────────→│
     │────── user_audio_chunk (base64) ────────→│
     │────── user_audio_chunk (base64) ────────→│
     │  ...  (streaming audio frames)           │
     │────── user_audio_chunk (base64) ────────→│
     │                                          │
     │────── [50 frames of silence] ───────────→│
     │                                          │  (Turn end signal)
     │                                          │
     │                                          │  [Server processes]
     │                                          │  [Generates response]
     │                                          │
     │←────── agent_response (text) ────────────│
     │←────── audio (base64 PCM) ───────────────│
     │←────── audio (base64 PCM) ───────────────│
     │  ...  (streaming audio chunks)           │
     │←────── audio (base64 PCM) ───────────────│
     │                                          │
     │←────── ping (event_id=123) ──────────────│
     │────── pong (event_id=123) ───────────────→│
     │                                          │
     │                                          │
     │  [User interrupts agent]                 │
     │────── user_audio_chunk ──────────────────→│
     │                                          │
     │←────── interruption ──────────────────────│
     │  [Agent stops generating]                │
     │                                          │
     │  ...  (more turns)                       │
     │                                          │
     │────── Close ─────────────────────────────→│
     │                                          │
```

### Ping/Pong Keepalive

```
maintain_pong() Task (main.py lines 197-238):

Purpose:
  - Prevent WebSocket idle timeout
  - Server sends ping every 60s if no activity
  - Client must respond with pong within timeout

Flow:

  [Task started]
    ↓
  while True:
    ↓
    raw = await ws.recv()  # Blocking wait
    ↓
    data = json.loads(raw)
    ↓
    [If type == "ping"]
      ↓
      event_id = data.get("event_id")
      delay_ms = data.get("ping_ms", 60000)
      ↓
      await send_pong(ws, event_id, delay_ms)
      ↓
      [pong sent]
    ↓
    [If type != "ping"]
      ↓
      [Discard message]  # Will be re-received by receive_response()
      ↓
      [NOTE: This causes mid-sentence audio bug if audio arrives!]
    ↓
  [Repeat]


Critical Issue Discovered in v1.0.6:
  - maintain_pong() was running DURING agent response
  - If audio message arrived while pong_task owned ws.recv(), it was DISCARDED
  - Result: Missing beginning of agent audio ("mid-sentence audio")

Fix in v1.0.7:
  - Cancel pong_task when user turn STARTS (button press or speech detection)
  - This ensures all agent audio arrives while receive_response() owns ws.recv()
  - No messages are discarded

Cancellation Points:
  - stream_audio_ptt(): Line 717-727 (on button press)
  - stream_audio_vad(): Line 857-867 (on speech detection)
  - After receive_response(): Line 1188 (restart for next turn)
```

---

## Race Condition Mitigation

### Problem: Multiple Consumers of ws.recv()

```
Scenario (Before v1.0.7 fix):

  Thread 1: maintain_pong()           Thread 2: receive_response()
      │                                      │
      │ ws.recv() ──→ [Blocks]               │
      │                                      │
      ├─ [Audio message arrives]             │
      ├─ Message received                    │
      ├─ type != "ping"                      │
      ├─ DISCARDED                           │
      │                                      │
      │                                      ├─ ws.recv() ──→ [Blocks]
      │                                      │
      │                                      ├─ [Next message arrives]
      │                                      ├─ (But first audio chunk was lost!)
```

### Solution: Task Cancellation

```
Flow (v1.0.7):

  [Idle between turns]
    ↓
  pong_task = asyncio.create_task(maintain_pong(ws))
    ↓
  [pong_task owns ws.recv(), responds to pings]
    ↓
  [User presses button OR speech detected]
    ↓
  stream_audio() called
    ↓
  IMMEDIATELY cancel pong_task:
    pong_task.cancel()
    await asyncio.wait_for(pong_task, timeout=0.5)
    ↓
  [pong_task cancelled, ws.recv() released]
    ↓
  [User audio streaming proceeds]
    ↓
  stream_audio() returns
    ↓
  receive_response() called
    ↓
  [receive_response() NOW owns ws.recv() exclusively]
    ↓
  [ALL audio messages arrive in receive_response()]
    ↓
  [No messages discarded]
    ↓
  receive_response() returns
    ↓
  pong_task = asyncio.create_task(maintain_pong(ws))
    ↓
  [Ready for next turn]
```

### Timing Diagram

```
Time: ──────────────────────────────────────────────────────────→

pong_task:  [RUNNING ──────────────]  [CANCELLED]  [IDLE]  [RESTARTED ────→

User Turn:                    [Button press]
                                    ↓
                              [Stream audio ──────]

Agent Turn:                                    [Receive response ────────]

WebSocket:   [pong owns recv] [cancelled]     [receive owns recv]  [pong owns recv]

Audio msgs:                                   [All audio received ✓]
```

### Additional Race Conditions Addressed

#### 1. State Machine Access

```python
# main.py lines 593-604

_STATE_LOCK = threading.Lock()

def get_state():
    with _STATE_LOCK:
        return STATE

def set_state(new_state):
    global STATE
    with _STATE_LOCK:
        old = STATE
        STATE = new_state
        log(f"State transition: {old} → {new_state}")
```

**Why needed:**
- Button thread checks state via callback
- Main thread modifies state
- Without lock: race condition on state read/write

#### 2. Serial Port Write

```python
# serial_com.py

_lock = threading.Lock()

def write(char):
    global _battery_shutdown_flag

    # Battery shutdown protection
    if _battery_shutdown_flag and char != 'D':
        return  # Don't overwrite 'D' animation

    with _lock:  # Thread-safe write
        if _port_fd is not None:
            try:
                os.write(_port_fd, char.encode())
            except Exception as e:
                # Reconnect logic
                pass
```

**Why needed:**
- Main thread sends display commands
- Battery thread sends 'V' (low voltage) or 'D' (shutdown)
- NFC thread sends 'N' (tag detected)
- Without lock: interleaved writes could corrupt display

#### 3. ALSA Device Cleanup

```python
# main.py lines 408-425

_open_pcms = []
_pcm_lock = threading.Lock()

def track_pcm(pcm):
    with _pcm_lock:
        _open_pcms.append(pcm)

def safe_close_all():
    with _pcm_lock:
        for pcm in _open_pcms:
            try:
                pcm.close()
            except Exception as e:
                log(f"Error closing PCM: {e}")
        _open_pcms.clear()
```

**Why needed:**
- Signal handler (SIGTERM) calls safe_close_all()
- Main thread may be accessing ALSA device concurrently
- Without lock: double-free or use-after-free possible

---

## Memory Management

### Memory Profile

Raspberry Pi Zero W 1.1: **512MB RAM**

Typical memory usage breakdown:

```
Component                     Memory Usage
────────────────────────────────────────────
Kernel + drivers              ~100 MB
systemd + base services       ~30 MB
Python interpreter            ~20 MB
main.py (idle)                ~40 MB
  - WebSocket connection      ~5 MB
  - Audio buffers             ~2 MB
  - asyncio event loop        ~10 MB
  - Imported modules          ~23 MB
battery_log.py                ~15 MB
tmpfs (/tmp)                  ~10 MB
Free (buffer/cache)           ~297 MB
────────────────────────────────────────────
Total                         ~512 MB
```

### Memory-Efficient Strategies

#### 1. Audio Buffer Management

```python
# Avoid large buffer accumulation

# BAD (unbounded growth):
out_buf = bytearray()
while True:
    chunk = await ws.recv()
    out_buf += chunk  # Could grow to hundreds of MB

# GOOD (bounded buffer):
out_buf = bytearray()
while True:
    chunk = await ws.recv()
    out_buf += chunk
    drain(out_buf)  # Immediately flush full frames to speaker

    # Monitor buffer growth
    if len(out_buf) > FRAME_BYTES * 10:  # >300ms buffered
        log(f"⚠️ Audio buffer growing: {len(out_buf)} bytes")
```

**Why it works:**
- `drain()` writes full frames immediately
- Only partial frames remain in buffer
- Typical `out_buf` size: 0-960 bytes (never more than 1 frame)

#### 2. String Accumulation

```python
# Avoid repeated string concatenation

# BAD (O(n²) memory allocations):
text = ""
for chunk in chunks:
    text = text + chunk  # Creates new string each time

# GOOD (O(n) with list):
text_chunks = []
for chunk in chunks:
    text_chunks.append(chunk)  # Append to list
full_text = "".join(text_chunks)  # Single join operation
```

#### 3. tmpfs for Volatile Data

```
/tmp mounted as tmpfs (RAM-backed filesystem):
  - Battery upload queue: /tmp/battery_queue.json
  - Runtime config: /tmp/aiflow.env
  - Logs (optional): /tmp/aiflow.log

Benefits:
  - Fast reads/writes (no SD card I/O)
  - Reduces SD card wear
  - Automatic cleanup on reboot

Trade-off:
  - Data lost on power loss
  - Consumes RAM (typically <10MB)
```

#### 4. Minimal Dependencies

```python
# Avoided heavyweight libraries:
#   - Django, Flask (web frameworks)
#   - NumPy, pandas (data analysis)
#   - OpenCV (computer vision)
#   - TensorFlow (ML)

# Used lightweight alternatives:
#   - Direct ALSA bindings (pyalsaaudio)
#   - Direct I2C (smbus)
#   - Direct serial (termios/fcntl)
#   - Built-in asyncio (no Twisted, Tornado)
```

### Memory Leak Prevention

```python
# 1. Close resources explicitly
try:
    mic = setup_mic()
    # ... use mic
finally:
    safe_close(mic, "mic")

# 2. Clear global references
globals()["LAST_MIC_METRICS"] = metrics  # Overwrite, don't append
globals().pop("NFC_TRIGGERED_TURN", False)  # Remove, don't accumulate

# 3. Bounded collections
_phrase_queue = deque(maxlen=16)  # Auto-discard old entries

# 4. Async task cleanup
try:
    await stream_audio(ws, pong_task)
except Exception as e:
    log(f"Error: {e}")
finally:
    if pong_task and not pong_task.done():
        pong_task.cancel()  # Don't leave orphaned tasks
```

### Future: Automatic Process Restart

```python
# Potential enhancement: Monitor memory usage and restart if threshold exceeded

import resource

def get_memory_mb():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024  # KB → MB

# In main loop:
if get_memory_mb() > 200:  # 200MB threshold
    log("Memory threshold exceeded, restarting process...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
```

---

## Power Management

### Power Budget

```
Component                    Current Draw (mA)    Notes
─────────────────────────────────────────────────────────────
Raspberry Pi Zero W 1.1      100-120 (idle)       Baseline (lower than Zero 2 W)
                             200-350 (active)     CPU-intensive tasks
USB Audio Interface          50-100               Microphone + speaker
PN532 NFC Reader             50-100               I2C communication
INA219 Power Monitor         1                    Minimal
GPIO Button                  <1                   Passive pull-up
LiPo Battery                 2000 mAh             Runtime: 60-90 minutes
─────────────────────────────────────────────────────────────
Total (idle)                 ~170 mA
Total (active conversation)  ~350 mA
Total (speaker playback)     ~500 mA              Peak current
```

### Voltage Drop Issue (Screen Glitch)

**Problem:**
- Speaker playback starts → sudden current draw
- Voltage on 5V rail drops momentarily (brownout)
- Screen glitches due to undervoltage

**Root Cause:**
- Insufficient power supply (< 2.5A)
- OR long/thin USB cable (voltage drop due to resistance)
- OR no bulk capacitor to smooth current spikes

**Mitigation Options:**

1. **Hardware:**
   - Use 5V 2.5A+ power supply
   - Shorter, thicker USB cable (lower resistance)
   - Add 1000-2200µF capacitor near speaker amp

2. **Software (limited effectiveness):**
   ```python
   # Gradual volume ramp when playback starts
   # (Not implemented yet, would require ALSA volume control)

   # Delay before first audio write
   if not saw_any_audio:
       await asyncio.sleep(0.05)  # 50ms delay
       saw_any_audio = True
   speaker.write(frame)
   ```

### Battery Monitoring

```
Three-stage protection:

┌─────────────────────────────────────────────────────────────┐
│  Stage 1: Pi Under-Voltage Detection (IMMEDIATE SHUTDOWN)   │
│                                                              │
│  vcgencmd get_throttled                                      │
│    └─→ Bits 0 or 16 set: Under-voltage detected             │
│        └─→ IMMEDIATE sudo poweroff                          │
│                                                              │
│  Rationale: Pi has detected voltage too low for stable      │
│             operation. Data corruption risk. Shut down NOW.  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Stage 2: Critical Voltage (GRACEFUL SHUTDOWN)              │
│                                                              │
│  INA219 voltage < 3.55V for 3 consecutive readings (90s)    │
│    └─→ Display 'D' (dead/shutdown animation)                │
│    └─→ Lock serial port (prevent overwrite of 'D')          │
│    └─→ sudo poweroff                                         │
│                                                              │
│  Rationale: Battery critically low. Avoid deep discharge     │
│             which can damage LiPo cells.                     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Stage 3: Low Voltage Warning (CONTINUE OPERATION)          │
│                                                              │
│  INA219 voltage < 3.65V for 3 consecutive readings (90s)    │
│    └─→ Display 'V' (battery low icon)                       │
│    └─→ Continue normal operation                            │
│                                                              │
│  Rationale: Warn user to charge soon, but still usable.     │
└─────────────────────────────────────────────────────────────┘
```

### Battery Percentage Calculation

```python
def voltage_to_percent(voltage):
    """
    LiPo discharge curve (simplified linear):
      4.15V = 100%
      3.55V = 0%

    Reality: Non-linear, but close enough for UI display.
    """
    v_min = 3.55
    v_max = 4.15
    v_range = v_max - v_min

    if voltage >= v_max:
        return 100.0
    elif voltage <= v_min:
        return 0.0
    else:
        return ((voltage - v_min) / v_range) * 100.0
```

### Power Saving Opportunities (Not Yet Implemented)

```
1. CPU Frequency Scaling:
   - Reduce CPU clock during idle (splash_idle state)
   - Scale up during conversation (running_agent state)
   - Command: echo powersave > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

2. Disable HDMI:
   - tvservice -o (save ~20mA)
   - Re-enable: tvservice -p

3. Reduce LED Brightness:
   - Disable activity LED: echo 0 > /sys/class/leds/led0/brightness

4. NFC Reader Power Control:
   - Disable PN532 during conversation (not waiting for tag)
   - Only enable between turns

5. Adaptive Audio Buffering:
   - Reduce period size during idle (lower latency, higher CPU)
   - Increase during conversation (lower CPU, higher latency)
```

---

## Filesystem Strategy

### Read-Only Root Filesystem

**Purpose:**
- Extend SD card lifespan (prevent write wear)
- Increase system stability (no filesystem corruption from power loss)
- Enable instant boot (no fsck required)

**Implementation:**

```
Mount options (/etc/fstab):

  /dev/mmcblk0p2  /  ext4  ro,noatime  0  0

tmpfs mounts (volatile):
  tmpfs  /tmp     tmpfs  defaults,noatime,nosuid,size=100m  0  0
  tmpfs  /var/tmp tmpfs  defaults,noatime,nosuid,size=10m   0  0
  tmpfs  /var/log tmpfs  defaults,noatime,nosuid,size=10m   0  0

Persistent mounts (read-write):
  /boot                              (config.txt, cmdline.txt)
  /etc/NetworkManager/system-connections  (WiFi credentials)
```

**Runtime Remount (for OTA updates):**

```python
# config_fetcher.py: check_and_apply_updates()

import subprocess

def remount_rw():
    """Remount root filesystem as read-write."""
    subprocess.run(["sudo", "mount", "-o", "remount,rw", "/"], check=True)

def remount_ro():
    """Remount root filesystem as read-only."""
    subprocess.run(["sudo", "mount", "-o", "remount,ro", "/"], check=True)

# Update flow:
try:
    remount_rw()
    # ... copy new files to /home/orb/AIflow/
    remount_ro()
except Exception as e:
    log(f"Update failed: {e}")
    remount_ro()  # Ensure we return to read-only state
    raise
```

**Alternative: `rwro` Tool**

```bash
# Install rwro (custom script for toggling RO/RW):
sudo apt install rwro  # Or custom script

# Usage:
rwro rw   # Switch to read-write
rwro ro   # Switch to read-only
rwro      # Show current state
```

### Persistent Data Locations

```
Config & Credentials:
  /home/orb/AIflow/.service_env         (RW: Device ID, API keys)
  /home/orb/AIflow/nfc_tags.json        (RW: NFC tag mappings, shared)
  /home/orb/AIflow/beep.wav             (RW: NFC feedback sound)
  /etc/NetworkManager/system-connections/ (RW: WiFi profiles)
  /boot/config.txt                      (RW: Hardware config)

Volatile Data (lost on reboot):
  /tmp/aiflow.env                       (tmpfs: Runtime config)
  /tmp/battery_queue.json               (tmpfs: Pending telemetry)
  /tmp/config_fetcher.log               (tmpfs: Startup logs)
```

### Data Preservation During OTA Updates

```python
# config_fetcher.py: check_and_apply_updates()

# Files to preserve (NOT overwritten by update):
# Note: Agent folders no longer needed - greeting is played live via WebSocket
PRESERVE_ITEMS = [
    "beep.wav",       # NFC scan feedback sound
    ".service_env",   # Service environment secrets (API keys, DEVICE_ID)
    "nfc_tags.json",  # NFC tag mappings (fallback if GitHub fetch fails)
]

# Update logic:
1. Download tarball to /tmp/
2. Extract to /tmp/AIflow_new/
3. Validate critical files exist
4. Backup current:
     cp -r /home/orb/AIflow /home/orb/AIflow.backup
5. Preserve config files to temp location
6. Replace AIflow/ with new code
7. Restore preserved files
8. Update version file
9. Clean up temp files
```

---

## Error Handling & Recovery

### Error Categories

```
1. Network Errors (transient, retryable)
   - Connection timeout
   - DNS resolution failure
   - WebSocket disconnect

2. Hardware Errors (device-specific, may require restart)
   - ALSA device busy
   - I2C communication failure
   - GPIO initialization error

3. API Errors (depends on status code)
   - 401 Unauthorized (bad API key, NOT retryable)
   - 429 Rate limit (retryable with backoff)
   - 500 Server error (retryable)

4. Configuration Errors (unrecoverable, require user intervention)
   - Missing DEVICE_ID
   - Invalid agent ID
   - No audio devices found

5. Critical Errors (immediate shutdown)
   - Battery under-voltage
   - Filesystem corruption
```

### Retry Strategies

#### Exponential Backoff

```python
# config_fetcher.py: fetch_config_from_api()

def fetch_with_retry(url, retries=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == retries - 1:
                raise  # Final attempt, give up

            delay = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
            log(f"Attempt {attempt+1} failed, retrying in {delay}s: {e}")
            time.sleep(delay)
```

#### Linear Retry with Timeout

```python
# nfc_backend.py: _nfc_loop()

def read_nfc_with_retry(pn532, timeout=0.2):
    """
    PN532 I2C reads can fail transiently.
    Retry up to 5 times before logging error.
    """
    for attempt in range(5):
        try:
            uid = pn532.read_passive_target(timeout=timeout)
            return uid
        except Exception as e:
            if attempt == 4:
                log(f"NFC read failed after 5 attempts: {e}")
                return None
            time.sleep(0.1)
```

### WebSocket Reconnection

```python
# main.py: run_session()

async def run_session():
    retry_delay = 1.0  # Start with 1s
    max_delay = 10.0   # Cap at 10s

    while get_state() == "running_agent":
        try:
            async with websockets.connect(WS_ENDPOINT, ...) as ws:
                # Connection successful, reset retry delay
                retry_delay = 1.0

                # Run conversation loop
                await conversation_loop(ws)

        except websockets.exceptions.ConnectionClosed as e:
            log(f"WebSocket closed: {e}")
            if get_state() != "running_agent":
                break  # User requested exit

            # Reconnect with exponential backoff
            log(f"Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)

        except Exception as e:
            log(f"Unexpected error: {e}")
            break  # Unrecoverable, exit session
```

### Graceful Degradation

```python
# Battery telemetry: Continue operation even if upload fails

def queue_upload(data):
    """Non-blocking telemetry upload."""
    try:
        # Add to queue
        queue.append(data)
        # Persist to disk (tmpfs, fast)
        with open("/tmp/battery_queue.json", "w") as f:
            json.dump(queue, f)
    except Exception as e:
        log(f"Queue upload failed: {e}")
        # Continue operation (telemetry not critical)

# NFC: Continue without tags if library fetch fails

def reload_tags():
    try:
        response = requests.get(tags_url, timeout=5)
        tags = response.json()
        log(f"Loaded {len(tags)} NFC tags from URL")
    except Exception as e:
        log(f"Failed to fetch tags from URL: {e}")
        try:
            # Fallback to local file
            with open(f"{base_dir}/{agent_id}/nfc_tags.json") as f:
                tags = json.load(f)
            log(f"Loaded {len(tags)} NFC tags from local file")
        except Exception as e2:
            log(f"Failed to load local tags: {e2}")
            tags = {}  # Continue with empty tags

    return tags
```

### Signal Handling

```python
# main.py: _shutdown()

def _shutdown(loop):
    """
    Graceful shutdown on SIGTERM/SIGINT.
    Close resources cleanly to prevent data corruption.
    """
    global STOP
    STOP = True  # Signal all loops to exit

    log("Received shutdown signal, cleaning up...")

    # Display goodbye animation
    serial_com.write('B')

    # Stop background threads
    mute_button.stop_mute_button()
    nfc.stop()

    # Cancel asyncio tasks
    for task in asyncio.all_tasks(loop):
        task.cancel()

    # Close audio devices
    safe_close_all()

    log("Cleanup complete, exiting")

# Register handlers
signal.signal(signal.SIGTERM, lambda s, f: _shutdown(asyncio.get_event_loop()))
signal.signal(signal.SIGINT, lambda s, f: _shutdown(asyncio.get_event_loop()))

# Also register atexit handler (for normal exit)
atexit.register(lambda: _shutdown(asyncio.get_event_loop()))
```

---

## Performance Characteristics

### Latency Analysis

```
Component                        Latency          Notes
──────────────────────────────────────────────────────────────────
Button press → Unmute            10-60ms          GPIO poll (10ms) + debounce (50ms)
Speech detection → Record        240ms            START_GATE_FRAMES (8 frames × 30ms)
Audio capture → WebSocket send   30-60ms          Frame buffer (30ms) + network RTT
ElevenLabs processing            500-2000ms       ASR + LLM + TTS generation
First audio chunk arrival        200-500ms        After ElevenLabs starts generating
Audio chunk → Speaker playback   0-30ms           drain() immediate, buffered by ALSA
User turn end → Agent start      100-300ms        Silence frames (1.5s) + processing
──────────────────────────────────────────────────────────────────
Total (PTT, button press → agent speaks):  1-3 seconds
Total (VAD, speech start → agent speaks):  1.5-3.5 seconds
```

### Throughput

```
Audio Streaming:

  Microphone → ElevenLabs:
    - Frame rate: 33.3 fps (30ms frames)
    - Data rate: 32 KB/s (960 bytes × 33.3)
    - Base64 overhead: ~33% → 43 KB/s
    - WebSocket overhead: ~10% → 47 KB/s
    - Network bandwidth: ~50 KB/s upstream

  ElevenLabs → Speaker:
    - Variable chunk size (1-10 KB typical)
    - Chunk rate: ~5-10 chunks/second
    - Data rate: 32 KB/s (PCM) + overhead
    - Network bandwidth: ~50 KB/s downstream

Total bandwidth (full-duplex conversation): ~100 KB/s (~800 Kbps)
```

### CPU Utilization

```
Task                       CPU %        Core Affinity
─────────────────────────────────────────────────────────
Idle (splash_idle)         5-10%        All cores
PTT recording              15-25%       Core 0 (main)
VAD recording              25-40%       Core 0 (main), Core 1 (VAD)
Audio playback             10-20%       Core 0 (main)
WebSocket I/O              5-10%        Core 0 (main)
Button polling             <1%          Core 1
NFC polling                2-5%         Core 2
Battery monitoring         <1%          Core 3 (separate process)
─────────────────────────────────────────────────────────
Peak (VAD + playback)      50-70%       Spread across cores
```

**Optimization Opportunities:**
- VAD processing is CPU-intensive (webrtcvad)
- PTT mode uses ~40% less CPU than VAD
- Consider hardware VAD (future) for lower power

---

## Design Decisions

### Why asyncio Instead of Threading for Main Loop?

**Decision:** Use `asyncio` for WebSocket and audio I/O in main thread.

**Rationale:**
- WebSocket library (websockets) is async-native
- Audio I/O has natural async points (recv, send, sleep)
- Avoid threading complexity (locks, race conditions)
- Better performance for I/O-bound tasks

**Trade-off:**
- Hardware threads still needed (button, NFC poll hardware)
- Mixing async and sync requires `run_coroutine_threadsafe()`

---

### Why Process Replacement (os.execv) Instead of Restart?

**Decision:** Use `os.execv()` to replace process image.

**Rationale:**
- Zero downtime (no service restart, preserves systemd supervision)
- Clean memory slate (prevents memory leaks from accumulating)
- Preserve PID (systemd tracking, log continuity)

**Trade-off:**
- More complex than `sys.exit()` + systemd restart
- Must carefully preserve environment and file descriptors

---

### Why Independent Battery Monitor Process?

**Decision:** battery_log.py as separate systemd service.

**Rationale:**
- Critical for hardware safety (prevent battery damage)
- Must remain functional even if main.py crashes
- Separation of concerns (monitoring vs. application)

**Trade-off:**
- Extra process overhead (~15MB RAM)
- Inter-process communication needed (serial port shared)

---

### Why Manual Ping/Pong Instead of Websockets Library?

**Decision:** Implement custom ping/pong handler (maintain_pong).

**Rationale:**
- Control over timing (cancel during user turn)
- Avoid library's automatic ping/pong (can't cancel)
- Debug visibility (log ping/pong messages)

**Trade-off:**
- More code to maintain
- Potential for bugs (fixed in v1.0.7)

---

### Why 1.5s Silence for Turn End?

**Decision:** Send 50 frames (1500ms) of silence to signal turn end.

**Rationale:**
- ElevenLabs server-side VAD needs ~1-1.5s to detect turn boundary
- Shorter durations (300ms) caused multiple questions merged as one
- Trade-off between latency and reliability

**Tested Values:**
- 300ms: Unreliable (turns not detected)
- 1000ms: Better but occasional misses
- 1500ms: Reliable turn detection

**Trade-off:**
- Adds 1.5s latency to every turn
- Alternative: Rely on server VAD only (not reliable enough)

---

### Why PTT and VAD Modes Instead of Just VAD?

**Decision:** Support both push-to-talk and voice activation.

**Rationale:**
- PTT: Lower CPU, no accidental triggers, explicit control
- VAD: Hands-free, more natural for extended conversations
- User preference varies by use case

**Implementation:**
- Shared `stream_audio()` router
- Mode-specific functions: `stream_audio_ptt()`, `stream_audio_vad()`
- Button behavior adapts to mode

---

## Known Limitations

### 1. Single WebSocket Connection

**Limitation:** Only one conversation session at a time.

**Impact:**
- Cannot have multiple concurrent conversations
- Cannot switch agents without ending session

**Potential Solution:**
- Implement connection pooling
- Allow quick agent switching (disconnect/reconnect)

---

### 2. No Acoustic Echo Cancellation

**Limitation:** Speaker audio can be picked up by microphone.

**Impact:**
- Agent may hear its own voice and respond to itself
- Requires user to be mindful of microphone placement

**Potential Solution:**
- Implement AEC (Acoustic Echo Cancellation) in software
- Use hardware AEC-enabled audio interface
- Mute microphone during agent playback (half-duplex)

---

### 3. Fixed Audio Format

**Limitation:** Hardcoded to 16kHz mono S16_LE.

**Impact:**
- Cannot use higher quality audio (24kHz, 48kHz)
- Reduced naturalness for TTS

**Potential Solution:**
- Make sample rate configurable
- Support multiple formats (negotiate with ElevenLabs)

---

### 4. No Offline Mode

**Limitation:** Requires internet connectivity for all operations.

**Impact:**
- Cannot function without WiFi
- Dependent on ElevenLabs API availability

**Potential Solution:**
- Local TTS/ASR fallback (e.g., Piper, Whisper)
- Cached responses for common queries
- Offline mode with limited functionality

---

### 5. Screen Glitch During Speaker Playback

**Limitation:** Voltage drop causes display glitch when speaker starts.

**Impact:**
- Visual distraction
- Indicates marginal power supply

**Solution:**
- Hardware: Better power supply, bulk capacitor
- Software: Gradual volume ramp (limited effectiveness)

---

### 6. No Multi-User Support

**Limitation:** No speaker identification or user profiles.

**Impact:**
- Cannot distinguish between multiple users
- All conversations treated as same user

**Potential Solution:**
- Add speaker identification (voice biometrics)
- NFC user profiles (scan tag to identify user)

---

## Future Enhancements

### 1. Hardware Improvements

```
- Better power supply (5V 3A, short cable)
- Bulk capacitor for speaker (1000-2200µF)
- Hardware AEC-enabled USB audio interface
- Larger battery (5000 mAh → 10+ hours runtime)
- Faster Pi (Zero 2 W+ or Pi 4)
```

### 2. Audio Processing

```
- Acoustic Echo Cancellation (software or hardware)
- Noise suppression (RNNoise, Krisp)
- Configurable sample rates (24kHz, 48kHz)
- Voice biometrics (speaker identification)
- Multi-channel audio (stereo output)
```

### 3. Conversation Features

```
- Multi-turn context preservation
- Interrupt handling (stop agent mid-sentence)
- Parallel conversations (multiple WebSocket connections)
- Quick agent switching (hot-swap without full reconnect)
- Conversation history export
```

### 4. System Features

```
- Automatic memory management (restart on threshold)
- Offline mode with local TTS/ASR
- Multi-language support
- Remote debugging (SSH tunnel, log streaming)
- A/B testing framework (compare agent versions)
```

### 5. Power Management

```
- CPU frequency scaling (powersave during idle)
- Adaptive NFC polling (disable during conversation)
- HDMI disable (save ~20mA)
- Sleep mode after N minutes of inactivity
- Wake-on-NFC (scan tag to wake from sleep)
```

### 6. Configuration

```
- Web UI for device configuration
- Mobile app for setup and control
- Over-the-air config push (no TEST tag needed)
- A/B config testing (rollback on failure)
- Per-agent volume presets
```

### 7. Monitoring & Telemetry

```
- Real-time dashboard (conversation metrics, battery, uptime)
- Error alerting (email, push notifications)
- Performance profiling (CPU, memory, latency)
- Audio quality metrics (PESQ, MOS)
- Usage analytics (turns per day, session duration)
```

### 8. NFC Enhancements

```
- NFC tag writing (provision tags from device)
- Tag groups (cycle through phrases)
- Conditional actions (time-based, location-based)
- Tag expiry (one-time use tags)
- Encrypted tag payloads (security)
```

---

## Conclusion

The Orb voice agent system demonstrates a well-architected embedded application with:
- Clear separation of concerns (battery, config, runtime)
- Robust error handling and recovery
- Efficient resource usage (memory, CPU, power)
- Production-ready features (OTA updates, telemetry, failsafes)

Key architectural strengths:
1. **Independence:** Battery monitoring cannot be taken down by application crashes
2. **Zero-downtime:** OTA updates via process replacement
3. **Defensive:** Retries, timeouts, graceful degradation
4. **Efficient:** Minimal dependencies, direct hardware access, bounded buffers

Recent improvements (v1.0.7):
- Fixed race condition causing mid-sentence audio loss
- Extended timeout for complex ElevenLabs responses
- Improved logging for debugging

The system is ready for production deployment and serves as a solid foundation for future enhancements.

---

**Document Version:** 1.0
**Last Updated:** 2025-01-15
**Authors:** Futurity Engineering, Claude (Anthropic)
