# The Orb - Voice Agent System

**Version:** v1.0.7
**Platform:** Raspberry Pi Zero W 1.1
**Status:** Production

---

## Overview

The Orb is a production-grade voice agent system running on Raspberry Pi Zero W 1.1 hardware, featuring real-time conversational AI powered by ElevenLabs' streaming API. The system provides a complete embedded voice interface with multi-modal input (push-to-talk, voice activation, NFC), automatic OTA updates, battery management, and cloud-based configuration.

### Key Features

- **Dual Input Modes**: PTT (Push-to-Talk) and VAD (Voice Activity Detection)
- **Real-time Audio Streaming**: 16kHz mono PCM bidirectional streaming via WebSocket
- **NFC Integration**: Hot-swappable tag library with custom phrase injection
- **OTA Updates**: Zero-downtime GitHub-based update system
- **Battery Management**: INA219-based monitoring with telemetry and auto-shutdown
- **Hot Reload**: Fast configuration refresh without full restart (2-3s)
- **Cloud Configuration**: Device settings managed via Supabase API
- **Production Ready**: Read-only filesystem, systemd services, robust error handling

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Hardware Requirements](#hardware-requirements)
3. [Software Dependencies](#software-dependencies)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Core Components](#core-components)
7. [Operating Modes](#operating-modes)
8. [API Integration](#api-integration)
9. [Service Architecture](#service-architecture)
10. [Data Flow](#data-flow)

---

## Project Structure

```
/home/orb/AIflow/
├── main.py                      # Main voice agent application (1,393 lines)
├── config_fetcher.py            # Startup config loader + OTA updater (823 lines)
├── serial_com.py                # Display serial communication (193 lines)
├── mute_button.py               # GPIO button handler (193 lines)
├── nfc_backend.py               # NFC tag reader (291 lines)
├── battery_log.py               # Battery monitoring service (332 lines)
├── INA219.py                    # INA219 sensor driver (214 lines)
│
├── config_fetcher.service       # Systemd service for startup
├── battery_log.service          # Systemd service for battery monitor
├── install_services.sh          # Service installer
├── check_services.sh            # Service status checker
│
├── .service_env                 # Service environment variables
├── version                      # Current version (v1.0.7)
│
├── {AGENT_ID}/                  # Per-agent data directories
│   ├── test.wav                 # Startup test audio
│   └── nfc_tags.json            # NFC tag mappings (hot-swappable)
│
├── beep.wav                     # NFC scan feedback sound
│
└── /tmp/
    ├── aiflow.env               # Runtime config (tmpfs)
    └── battery_queue.json       # Pending telemetry uploads (tmpfs)
```

### File Descriptions

| File | Purpose | Critical |
|------|---------|----------|
| `main.py` | Core application: WebSocket streaming, audio I/O, turn management | ✓ |
| `config_fetcher.py` | Startup orchestrator: config fetch, OTA updates, process handoff | ✓ |
| `battery_log.py` | Independent battery monitor with telemetry and auto-shutdown | ✓ |
| `serial_com.py` | Display controller communication (animations, status) | ✓ |
| `mute_button.py` | GPIO button with mode-aware behavior (PTT/VAD) | ✓ |
| `nfc_backend.py` | PN532 NFC reader with hot-reload capability | ✓ |
| `INA219.py` | I2C battery sensor driver (voltage, current, power) | ✓ |
| `.service_env` | Device ID and API keys (persisted) | ✓ |
| `version` | Semantic version string for OTA updates | ✓ |

---

## Hardware Requirements

### Computing Platform

- **Board:** Raspberry Pi Zero W 1.1 (BCM2835 ARM11, 512MB RAM, single-core 1GHz)
- **Storage:** 8GB+ microSD card (read-only mode compatible)
- **Filesystem:** tmpfs for `/tmp` (volatile storage)
- **OS:** Raspberry Pi OS Lite (Debian-based)

### Audio Hardware

- **Interface:** USB audio adapter (ALSA-compatible)
- **Microphone:** 16kHz mono, S16_LE format
- **Speaker:** 16kHz mono output
- **Device Path:** `plughw:0,0` (configurable via `MIC_DEVICE`/`SPK_DEVICE`)
- **Volume Control:** ALSA mixer ("Speaker" control)

### Input Devices

| Component | Interface | Specification |
|-----------|-----------|---------------|
| **GPIO Button** | GPIO12 (D12) | Active-low, internal pull-up, 50ms debounce |
| **NFC Reader** | I2C (SDA/SCL) | PN532 chipset, I2C address 0x24 |

### Power System

| Component | Interface | Purpose |
|-----------|-----------|---------|
| **INA219 Sensor** | I2C address 0x43 | Voltage/current/power monitoring |
| **Battery** | Direct | LiPo 3.55V-4.15V range |
| **Voltage Thresholds** | - | Low: 3.65V, Critical: 3.55V |

### Display

- **Interface:** Serial UART (USB or direct UART)
- **Protocol:** Single-byte commands (115200 baud, 8N1)
- **Commands:** S, L, U, M, O, N, V, D, B (splash, loading, unmuted, muted, agent speaking, NFC, voltage low, dead, bye)

### Pin Mapping

```
GPIO12 (D12)  → Mute Button (active-low with pull-up)
GPIO2  (SDA)  → I2C Data   (PN532 NFC + INA219)
GPIO3  (SCL)  → I2C Clock  (PN532 NFC + INA219)
/dev/ttyUSB0  → Serial Display (or fallback: ttyACM0, serial0, ttyAMA0, ttyS0)
```

---

## Software Dependencies

### Python Version

- **Required:** Python 3.9+
- **Virtual Environment:** `/home/orb/env/` (recommended)

### Standard Library Modules

```python
asyncio          # Async WebSocket and event loop
threading        # Background threads (button, NFC, battery)
json, base64     # Data serialization
time, os, sys    # System utilities
signal, atexit   # Process lifecycle management
wave             # Audio file playback
subprocess       # System command execution (amixer, vcgencmd)
collections      # deque for audio buffering
```

### External Python Libraries

| Package | Version | Purpose |
|---------|---------|---------|
| `websockets` | Latest | ElevenLabs WebSocket client |
| `requests` | Latest | HTTP API calls (config, telemetry, OTA) |
| `webrtcvad` | Latest | Voice activity detection (WebRTC VAD) |
| `pyalsaaudio` | Latest | ALSA audio capture/playback |
| `python-dotenv` | Latest | Environment variable loading |
| `smbus` | Latest | I2C communication (INA219) |
| `adafruit-blinka` | Latest | CircuitPython compatibility layer |
| `adafruit-circuitpython-pn532` | Latest | PN532 NFC reader driver |

### System Dependencies

```bash
# Audio
libasound2        # ALSA library
alsa-utils        # amixer volume control

# System utilities
network-manager   # nmcli for WiFi
i2c-tools         # I2C debugging (i2cdetect)
libraspberrypi-bin # vcgencmd (voltage, temp, throttling)

# Permissions
# Add user to groups: audio, i2c, gpio, dialout
```

### Installation Command

```bash
pip install websockets requests webrtcvad pyalsaaudio python-dotenv smbus \
            adafruit-blinka adafruit-circuitpython-pn532
```

---

## Installation

### System Preparation

```bash
# 1. Enable I2C
sudo raspi-config
# → Interface Options → I2C → Enable

# 2. Add user to required groups
sudo usermod -aG audio,i2c,gpio,dialout orb

# 3. Configure audio devices in /boot/config.txt
# (Device-specific, ensure USB audio is recognized)

# 4. Set up read-only filesystem (optional, for production)
# Use rwro tool or custom overlayfs setup
```

### Application Installation

```bash
# 1. Clone repository
cd /home/orb
git clone <repository-url> AIflow
cd AIflow

# 2. Create virtual environment
python3 -m venv /home/orb/env
source /home/orb/env/bin/activate

# 3. Install dependencies
pip install websockets requests webrtcvad pyalsaaudio python-dotenv smbus \
            adafruit-blinka adafruit-circuitpython-pn532

# 4. Configure environment variables
nano .service_env
# Add:
#   DEVICE_ID=your_device_id
#   LOVABLE_API_KEY=your_supabase_key
#   ELEVENLABS_API_KEY=your_elevenlabs_key (optional, fetched from cloud)

# 5. Install systemd services
chmod +x install_services.sh
sudo ./install_services.sh

# 6. Verify services
./check_services.sh

# 7. Reboot
sudo reboot
```

### Service Management

```bash
# Check status
sudo systemctl status battery_log.service
sudo systemctl status config_fetcher.service

# View logs
sudo journalctl -u battery_log.service -f
sudo journalctl -u config_fetcher.service -f

# Restart
sudo systemctl restart config_fetcher.service

# Enable/disable
sudo systemctl enable battery_log.service
sudo systemctl disable config_fetcher.service
```

---

## Configuration

### Environment Variables (.service_env)

Persistent configuration stored in `/home/orb/AIflow/.service_env`:

```bash
DEVICE_ID=orb_001                          # Unique device identifier (required)
LOVABLE_API_KEY=your_supabase_key          # Supabase API key for telemetry
ELEVENLABS_API_KEY=your_key                # ElevenLabs API key (optional, cloud fallback)
PYTHONUNBUFFERED=1                         # Immediate log output
```

### Runtime Configuration (/tmp/aiflow.env)

Volatile configuration created by `config_fetcher.py` on boot:

```bash
AGENT_ID=uHlKfBtzRYokBFLcCOjq              # ElevenLabs agent ID
VOLUME=7                                    # System volume (1-10)
INPUT_MODE=PTT                              # PTT or VAD
DEVICE_NAME=The Orb                         # Human-readable name
WIFI_SSID=your_network                      # WiFi credentials
WIFI_PASSWORD=your_password
```

### Agent Name Mapping

Cloud config uses human-readable names, mapped to ElevenLabs agent IDs:

```python
AGENT_MAP = {
    "Zane": "uHlKfBtzRYokBFLcCOjq",
    "Rowan": "agent_01jvs5f45jepab76tr81m51gdx",
    "Nova": "agent_1701k5bgdzmte5f9q518mge3jsf0",
    "Cypher": "agent_01jvwd88bdeeftgh3kxrx1k4sk"
}
```

### Volume Calibration (1-10 scale → ALSA raw values)

```python
VOLUME_MAP = {
    10: 120,  # Maximum
    9:  117,
    8:  113,
    7:  109,
    6:  103,
    5:  95,
    4:  84,
    3:  64,
    2:  44,
    1:  0     # Minimum (not muted, just very quiet)
}
```

### Audio Constants (main.py lines 44-53)

```python
RATE = 16000                               # Sample rate (Hz)
CHANNELS = 1                               # Mono
FORMAT = alsaaudio.PCM_FORMAT_S16_LE       # 16-bit signed little-endian
FRAME_MS = 30                              # Frame duration (milliseconds)
SAMPLES_PER_FRAME = 480                    # 16000 Hz × 30ms / 1000
BYTES_PER_SAMPLE = 2                       # 16-bit = 2 bytes
FRAME_BYTES = 960                          # 480 samples × 2 bytes
```

### VAD Parameters (main.py lines 55-64)

```python
VAD_MODE = 3                               # WebRTC VAD aggressiveness (0-3, 3=most aggressive)
MIN_SPOKEN_MS = 600                        # Minimum speech duration to be valid (ms)
SILENCE_END_MS = 1500                      # Silence duration to trigger turn end (ms)
PREROLL_FRAMES = 5                         # Frames to buffer before speech detection
START_GATE_FRAMES = 8                      # Consecutive speech frames required to start (240ms)

# Derived
MIN_CHUNKS = 20                            # 600ms / 30ms
END_SILENCE_CHUNKS = 50                    # 1500ms / 30ms (1.5 seconds of silence)
```

### Response Handling (main.py lines 66-70)

```python
FIRST_CONTENT_MAX = 15.0                   # Max wait for first agent response (seconds)
                                           # Increased from 5.0 to accommodate complex responses
CONTENT_IDLE = 0.15                        # Idle time after last content (seconds)
GRACE_DRAIN = 0.15                         # Final sweep for straggler messages (seconds)
FIRST_TURN_BARGE_AFTER_MS = 500            # Barge-in delay for first turn greeting (ms)
```

### Battery Thresholds (battery_log.py)

```python
LOW_VOLTAGE_THRESHOLD = 3.65               # Show low battery warning (volts)
CRITICAL_VOLTAGE_THRESHOLD = 3.55          # Trigger shutdown (volts)
CHECK_INTERVAL = 30                        # Battery check frequency (seconds)
UPLOAD_INTERVAL = 90                       # Telemetry upload frequency (seconds)
LOW_COUNT_THRESHOLD = 3                    # Consecutive low readings before warning
CRITICAL_COUNT_THRESHOLD = 3               # Consecutive critical readings before shutdown
```

### Network Endpoints

```python
# Configuration API
CONFIG_API = "https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/get-device-config?device_id={DEVICE_ID}"

# Battery telemetry API
BATTERY_API = "https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/update-battery"

# NFC tag library (hot-swappable)
NFC_TAGS_URL = "https://raw.githubusercontent.com/CollaboratorFuturity/futuresGarden/main/nfc_tags.json"

# OTA updates
GITHUB_RELEASES = "https://api.github.com/repos/CollaboratorFuturity/futuresGarden/releases/latest"

# ElevenLabs WebSocket
ELEVENLABS_WS = "wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT_ID}"
```

---

## Core Components

### main.py - Voice Agent Application

**Purpose:** Real-time conversational AI with bidirectional audio streaming over WebSocket.

**Key Responsibilities:**
- WebSocket connection management with ElevenLabs
- Audio capture and streaming (microphone → ElevenLabs)
- Audio playback (ElevenLabs → speaker)
- Turn-taking coordination (user turn → agent turn → repeat)
- State machine management (splash_idle, running_agent)
- NFC tag handling and hot reload
- Signal handling and graceful shutdown

**Major Functions:**

| Function | Lines | Purpose |
|----------|-------|---------|
| `main_control_loop()` | 1318-1393 | Main state machine loop |
| `run_session()` | 1099-1201 | WebSocket session with conversation loop |
| `stream_audio_ptt()` | 653-769 | Push-to-talk audio streaming |
| `stream_audio_vad()` | 771-887 | Voice activity detection audio streaming |
| `receive_response()` | 889-1097 | Agent response reception and playback |
| `maintain_pong()` | 197-238 | Background keepalive task (60s interval) |
| `hot_reload_config()` | 1237-1303 | Fast config refresh (TEST tag) |

**Audio Processing Pipeline:**

```
Microphone
    ↓
ALSA Capture (setup_mic, line 394)
    ↓
30ms frame buffering (960 bytes)
    ↓
WebRTC VAD analysis (is_speech_exact, line 387) [VAD mode only]
    ↓
Base64 encoding
    ↓
WebSocket send (send_user_json, line 240)
    ↓
ElevenLabs API
```

```
ElevenLabs API
    ↓
WebSocket receive (ws.recv(), line 1023)
    ↓
JSON parsing (typ="audio", line 1037)
    ↓
Base64 decode (line 1040)
    ↓
Frame buffering (out_buf, line 1041)
    ↓
ALSA Playback (drain, line 963)
    ↓
Speaker
```

---

### config_fetcher.py - Startup Configuration & OTA Updater

**Purpose:** Orchestrates device startup, configuration loading, OTA updates, and process handoff.

**Key Responsibilities:**
- Network connectivity validation (60s timeout)
- Cloud configuration fetching (Supabase API)
- Agent name → ID mapping
- System volume application (amixer)
- WiFi provisioning (NetworkManager/nmcli)
- OTA update checking and installation
- Environment file generation (/tmp/aiflow.env)
- Process replacement handoff to main.py (os.execv)

**Update Pipeline:**

```
Boot
  ↓
config_fetcher.service starts
  ↓
Wait for network (60s timeout)
  ↓
Fetch config from Supabase API (5 retries, 10s timeout)
  ↓
Check GitHub releases for updates
  ↓
[If update available]
  ├─ Download tarball
  ├─ Validate (check critical files)
  ├─ Backup current installation
  ├─ Extract to AIflow/
  ├─ Preserve agent data folders
  ├─ Update version file
  └─ [If validation fails: restore backup]
  ↓
Apply WiFi credentials (if changed)
  ↓
Set system volume via amixer
  ↓
Write /tmp/aiflow.env
  ↓
os.execv("/home/orb/env/bin/python", [main.py])
  ↓
main.py takes over
```

**Major Functions:**

| Function | Purpose |
|----------|---------|
| `main()` | Entry point orchestrating full startup sequence |
| `wait_for_network(timeout)` | Poll network connectivity with exponential backoff |
| `fetch_config_from_api(url, retries)` | HTTP GET with retry logic and timeout |
| `map_agent_name_to_id(agent_name)` | Convert human names to ElevenLabs agent IDs |
| `write_env_file(config, env_path)` | Generate /tmp/aiflow.env from cloud config |
| `apply_system_volume(config)` | Set ALSA mixer volume via amixer |
| `configure_wifi(ssid, password)` | NetworkManager WiFi provisioning |
| `check_and_apply_updates()` | Complete OTA update pipeline |
| `transition_to_main_app(main_py_path)` | Process replacement via os.execv |

---

### battery_log.py - Battery Monitoring Service

**Purpose:** Independent background service for battery monitoring, telemetry, and safe shutdown.

**Key Responsibilities:**
- INA219 voltage/current monitoring (30s intervals)
- Dual-averaged readings (50ms apart, ±0.02V accuracy)
- System health telemetry (CPU temp, memory, throttling)
- Async upload queue with persistent storage
- Three-stage shutdown logic:
  1. Pi under-voltage detection (immediate)
  2. Critical voltage (3.55V, 3 consecutive readings)
  3. Low voltage warning (3.65V, display icon)
- Graceful poweroff with display animation

**Monitoring Flow:**

```
battery_log.service starts (independent of main app)
  ↓
Initialize INA219 sensor (I2C 0x43)
  ↓
[Every 30 seconds]
  ├─ Read voltage (dual-averaged, 50ms apart)
  ├─ Read current
  ├─ Calculate percentage (3.55V=0%, 4.15V=100%)
  ├─ Get system health (vcgencmd, free)
  ├─ Check Pi under-voltage flag
  ├─ [If under-voltage detected]
  │   └─ IMMEDIATE SHUTDOWN
  ├─ [If voltage < 3.55V for 3 consecutive readings]
  │   ├─ Display 'D' animation (dead)
  │   └─ sudo poweroff
  ├─ [If voltage < 3.65V for 3 consecutive readings]
  │   └─ Display 'V' animation (battery low warning)
  └─ Queue upload to Supabase (async, non-blocking)
  ↓
[Every 90 seconds]
  └─ Process upload queue (batch telemetry, retry on failure)
```

**Major Functions:**

| Function | Purpose |
|----------|---------|
| `main()` | Main monitoring loop with shutdown detection |
| `get_averaged_voltage(ina, samples, delay_ms)` | Stable voltage reading via dual-average |
| `queue_upload(percent, voltage, temperature)` | Non-blocking telemetry queueing |
| `get_system_health()` | CPU temp, memory, throttling status |
| `safe_shutdown()` | Graceful poweroff with display notification |

---

### serial_com.py - Display Communication

**Purpose:** Lightweight serial port manager for display controller communication.

**Key Features:**
- No pyserial dependency (direct termios/fcntl)
- Thread-safe with lock
- Auto-reconnect on errors
- Fallback port detection
- Non-blocking writes
- Battery shutdown protection (locks to 'D' command)

**Display Commands:**

| Command | Meaning | When Used |
|---------|---------|-----------|
| `S` | Splash | Idle state (waiting for NFC tag) |
| `L` | Loading | Processing, waiting for agent response |
| `U` | Unmuted | Recording user audio (listening) |
| `M` | Muted | Microphone muted |
| `O` | Agent speaking | Agent audio playback in progress |
| `N` | NFC | NFC tag detected (brief feedback) |
| `V` | Voltage low | Battery warning (< 3.65V) |
| `D` | Dead | Critical shutdown (< 3.55V or under-voltage) |
| `B` | Bye | Graceful shutdown (SIGTERM) |

**Major Functions:**

| Function | Purpose |
|----------|---------|
| `write(char)` | Send single-byte command (thread-safe, non-blocking) |
| `open_port(port, baud)` | Configure termios, set raw mode |
| `close_port()` | Clean port closure |
| `configure(port, baud)` | Runtime port/baud reconfiguration |

---

### mute_button.py - GPIO Button Handler

**Purpose:** Mode-aware button handler with PTT and VAD behavior differentiation.

**PTT Mode Behavior:**
- **Press (unmute):** Start recording immediately
- **Hold ≥ 1s:** Valid recording, send audio to agent
- **Hold < 1s:** Silent revert (no turn end, anti-bounce)
- **Release (mute):** End turn immediately

**VAD Mode Behavior:**
- **Press:** Toggle mute (pause/resume recording)
- **Release:** Ignored
- **No turn control:** Turn ends via silence detection only

**Key Features:**
- 50ms debounce filter (mechanical bounce suppression)
- State check callback (only active in `running_agent` state)
- `force_turn_end` event for external interrupts (NFC)
- Thread-safe mute state access
- Programmatic mute override

**Major Functions:**

| Function | Purpose |
|----------|---------|
| `start_mute_button(pin, debounce_s, poll_s)` | Initialize GPIO thread (10ms poll rate) |
| `is_muted()` | Get current mute state (thread-safe) |
| `stop_mute_button()` | Cleanup and thread join |
| `set_mode(mode)` | Switch between PTT/VAD behavior |
| `set_state_check(callback)` | Set state validation function |
| `force_mute()` | Programmatically set mute state |
| `trigger_force_turn_end()` | Signal turn end (NFC interrupt) |

---

### nfc_backend.py - NFC Tag Reader

**Purpose:** Background PN532 NFC reader with hot-swappable tag library.

**Key Features:**
- I2C communication (address 0x24)
- UID normalization (XX:XX:XX format)
- JSON tag library (local file or GitHub URL)
- 1.5s debounce (prevent double-reads)
- WebSocket integration via asyncio.run_coroutine_threadsafe
- Queue-based phrase buffering (maxlen=16)
- Enable/disable per conversation turn
- Retry logic (5 attempts with exponential backoff)
- Firmware version validation

**Special Tags:**

| Tag Name | Behavior |
|----------|----------|
| `TEST` | Trigger hot reload (fetch fresh config, update agent/volume/mode) |
| `AGENT_START` | Transition from splash_idle → running_agent (begin conversation) |
| Custom phrases | Inject text into active conversation as user message |

**Tag Library Format (nfc_tags.json):**

```json
{
  "XX:XX:XX:XX": "What is the weather today?",
  "YY:YY:YY:YY": "Tell me a joke",
  "TEST": "TEST",
  "AGENT_START": "AGENT_START"
}
```

**Major Functions:**

| Function | Purpose |
|----------|---------|
| `NfcReader(agent_id, base_dir, debounce_s, log, tags_url, callback)` | Constructor |
| `start()` / `stop()` | Thread lifecycle control |
| `enable()` / `disable()` | Scanning control (per turn) |
| `set_sender(ws, loop)` | Attach WebSocket for phrase injection |
| `reload_tags()` | Refresh tag library from URL or file |

---

### INA219.py - Battery Sensor Driver

**Purpose:** Pure smbus driver for INA219 current/voltage/power sensor.

**Key Features:**
- I2C communication (default address 0x40, configurable)
- 16V/5A calibration preset
- 12-bit ADC with 32-sample averaging
- No CircuitPython dependency (minimal overhead)

**Major Functions:**

| Function | Returns | Purpose |
|----------|---------|---------|
| `INA219(i2c_bus, addr)` | - | Constructor (initialize sensor) |
| `getBusVoltage_V()` | float | Load voltage (battery voltage, 3-4.2V for LiPo) |
| `getCurrent_mA()` | float | Current draw (mA) |
| `getShuntVoltage_mV()` | float | Shunt voltage (mV) |
| `getPower_W()` | float | Power consumption (watts) |

---

## Operating Modes

### PTT (Push-to-Talk) Mode

**Characteristics:**
- Button-controlled recording
- No VAD processing (faster, lower CPU)
- Immediate start/stop
- 1s minimum press duration (anti-bounce)
- Explicit turn boundaries

**Flow:**

```
Idle (waiting for button press)
  ↓
[Button PRESS]
  ↓
150ms stabilization delay (power rail)
  ↓
Display 'U' (unmuted)
  ↓
Start recording + streaming to ElevenLabs
  ↓
[Button RELEASE]
  ↓
Send 1.5s silence (50 frames of zeros)
  ↓
Display 'L' (loading)
  ↓
Wait for agent response
  ↓
Play agent audio
  ↓
Return to idle
```

**Configuration:**
```bash
INPUT_MODE=PTT  # In /tmp/aiflow.env
```

---

### VAD (Voice Activity Detection) Mode

**Characteristics:**
- Hands-free operation
- Automatic speech detection
- WebRTC VAD processing
- Silence-based turn ending
- Button acts as toggle mute

**Flow:**

```
Idle (continuous listening)
  ↓
Display 'U' (unmuted/listening)
  ↓
[Voice detected: 8 consecutive speech frames = 240ms]
  ↓
Start recording + streaming (with 5-frame preroll buffer)
  ↓
Continue until 1500ms silence detected
  ↓
Send 1.5s silence (turn end signal)
  ↓
Display 'L' (loading)
  ↓
Wait for agent response
  ↓
Play agent audio
  ↓
Return to idle (continuous listening)
```

**Configuration:**
```bash
INPUT_MODE=VAD  # In /tmp/aiflow.env
```

**VAD Parameters:**
- **Start gate:** 8 consecutive speech frames (240ms)
- **Minimum duration:** 600ms total speech
- **Silence threshold:** 1500ms silence to end
- **Preroll buffer:** 5 frames (150ms) sent retroactively

---

### Mode Comparison

| Feature | PTT | VAD |
|---------|-----|-----|
| **Activation** | Button press | Automatic speech detection |
| **Turn End** | Button release | 1500ms silence |
| **Button Role** | Momentary (hold to talk) | Toggle mute |
| **CPU Usage** | Lower (no VAD) | Higher (WebRTC VAD) |
| **Latency** | Immediate | 240ms gate delay |
| **User Interaction** | Active (button hold) | Passive (just speak) |
| **Short Utterances** | Supported (no minimum) | Gated (600ms minimum) |
| **Accidental Triggers** | None (button required) | Possible (background noise) |

---

## Static Mode (No Cloud Dependency)

For devices that need to operate independently without cloud configuration, a **Static Mode** is available using the `static_launcher.service`.

### Overview

Static Mode bypasses the cloud-based configuration system (`config_fetcher.service`) and uses hardcoded values written directly to `/tmp/aiflow.env` on boot.

### Use Cases

- **Standalone devices** that don't need remote configuration
- **Air-gapped deployments** without internet access
- **Demo/kiosk devices** with fixed agent and settings
- **Development/testing** with specific configurations

### Implementation

#### 1. Create Static Launcher Script

Create `/home/orb/AIflow/static_launcher.sh`:

```bash
#!/bin/bash

# Create static config in tmpfs
cat > /tmp/aiflow.env << 'EOF'
AGENT_ID=uHlKfBtzRYokBFLcCOjq
VOLUME=9
INPUT_MODE=PTT
DEVICE_ID=static_orb_001
DEVICE_NAME=Static Orb
WIFI_SSID=
WIFI_PASSWORD=
AUTO_START=true
EOF

# Set system volume (volume 9 = ALSA raw value 117)
amixer set Speaker 117 > /dev/null 2>&1

# Launch main.py
cd /home/orb/AIflow
exec /home/orb/env/bin/python main.py
```

#### 2. Create Static Service

Create `/etc/systemd/system/static_launcher.service`:

```ini
[Unit]
Description=AIflow Static Mode (No API)
After=network.target

[Service]
Type=simple
User=orb
WorkingDirectory=/home/orb/AIflow
EnvironmentFile=/home/orb/AIflow/.service_env
ExecStart=/home/orb/AIflow/static_launcher.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

#### 3. Enable Static Mode

```bash
# Disable cloud-based service
sudo systemctl disable config_fetcher.service
sudo systemctl stop config_fetcher.service

# Enable static service
sudo systemctl daemon-reload
sudo systemctl enable static_launcher.service
sudo systemctl start static_launcher.service
```

### Static Mode Features

| Feature | Standard Mode | Static Mode |
|---------|---------------|-------------|
| **Configuration Source** | Supabase API | Hardcoded in script |
| **Agent ID** | Cloud-managed | Fixed in script |
| **Volume** | Cloud-managed | Fixed in script |
| **WiFi Credentials** | Cloud-managed | Optional in script |
| **OTA Updates** | Automatic | Manual only |
| **Hot Reload (TEST tag)** | Updates from cloud | N/A (no API) |
| **Network Dependency** | Required on boot | Optional (ElevenLabs only) |
| **AUTO_START** | Configurable | Enabled (skips splash screen) |
| **NFC Tags** | Downloads from GitHub | Downloads from GitHub (fallback to local) |

### AUTO_START Feature

When `AUTO_START=true` is set in `/tmp/aiflow.env`, the device will:

1. Skip the splash screen (`splash_idle` state)
2. Immediately enter `running_agent` state
3. Connect to ElevenLabs WebSocket
4. Start conversation without requiring NFC tag scan

This is ideal for kiosk/demo devices that should be ready to use immediately on boot.

### NFC Behavior in Static Mode

- NFC tags still download from GitHub (independent of config_fetcher)
- Falls back to local file: `/home/orb/AIflow/{AGENT_ID}/nfc_tags.json`
- TEST tag does NOT trigger hot reload (no cloud API access)
- AGENT_START tag is unnecessary (AUTO_START enabled)
- Custom phrase tags work normally

### Switching Between Modes

**To Standard Mode:**
```bash
sudo systemctl disable static_launcher.service
sudo systemctl enable config_fetcher.service
sudo systemctl restart config_fetcher.service
```

**To Static Mode:**
```bash
sudo systemctl disable config_fetcher.service
sudo systemctl enable static_launcher.service
sudo systemctl restart static_launcher.service
```

### Notes

- `DEVICE_ID` must still be in `.service_env` (for battery telemetry)
- `battery_log.service` runs independently in both modes
- To change agent/volume in Static Mode, edit `static_launcher.sh` and restart service
- Static devices still require network for ElevenLabs API

---

## API Integration

### ElevenLabs Conversational AI WebSocket

**Endpoint:**
```
wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AGENT_ID}
```

**Connection:**
```python
ws = await websockets.connect(
    WS_ENDPOINT,
    extra_headers={"xi-api-key": API_KEY},
    ping_interval=None  # Manual ping/pong handling
)
```

**Message Types (Client → Server):**

| Type | Payload | Purpose |
|------|---------|---------|
| `user_audio_chunk` | `{"user_audio_chunk": base64_pcm}` | Send audio frame |
| `pong` | `{"type": "pong", "event_id": int, ...}` | Keepalive response |
| `user_message` | `{"type": "user_message", "text": str}` | Inject text (NFC) |
| `conversation_initiation_client_data` | `{"conversation_config_override": ...}` | Suppress greeting |

**Message Types (Server → Client):**

| Type | Fields | Purpose |
|------|--------|---------|
| `audio` | `audio_event.audio_base_64` | Agent speech audio chunk |
| `agent_response` | `agent_response_event.agent_response` | Agent transcript (text) |
| `user_transcript` | `user_transcript_event.user_transcript` | User transcript (text) |
| `ping` | `event_id` | Keepalive request |
| `interruption` | - | Agent interrupted by user |
| `agent_response_correction` | - | Agent corrected previous response |

**Keepalive (Ping/Pong):**

```python
# Server sends:
{"type": "ping", "event_id": 1234, "ping_ms": 60000}

# Client responds within 60s:
{"type": "pong", "event_id": 1234}

# Implementation: maintain_pong() task (main.py lines 197-238)
# - Runs in background during idle
# - Cancelled during user turn (to prevent message consumption)
# - Restarted after agent response completes
```

---

### Supabase Configuration API

**Endpoint:**
```
GET https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/get-device-config
```

**Query Parameters:**
```
?device_id={DEVICE_ID}
```

**Headers:**
```
Authorization: Bearer {LOVABLE_API_KEY}
```

**Response Format:**
```json
{
  "agent_name": "Zane",
  "volume": 7,
  "input_mode": "PTT",
  "device_name": "The Orb",
  "wifi_ssid": "network_name",
  "wifi_password": "password123"
}
```

**Retry Logic:**
- 5 attempts
- 10s timeout per attempt
- Exponential backoff: 2s, 4s, 8s, 16s, 32s

---

### Supabase Battery Telemetry API

**Endpoint:**
```
POST https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/update-battery
```

**Headers:**
```
Authorization: Bearer {LOVABLE_API_KEY}
Content-Type: application/json
```

**Payload:**
```json
{
  "device_id": "orb_001",
  "battery_percent": 87.5,
  "voltage": 3.98,
  "current_ma": 150.2,
  "power_w": 0.597,
  "temperature_c": 45.3,
  "memory_mb": 123,
  "throttled": false,
  "timestamp": "2025-01-15T12:34:56.789Z"
}
```

**Upload Strategy:**
- Queue-based (persistent to /tmp/battery_queue.json)
- Batch upload every 90s
- Non-blocking (separate thread)
- Retry on failure (3 attempts, 5s timeout)

---

### GitHub Releases API (OTA Updates)

**Endpoint:**
```
GET https://api.github.com/repos/CollaboratorFuturity/futuresGarden/releases/latest
```

**Response Fields Used:**
```json
{
  "tag_name": "v1.0.8",
  "tarball_url": "https://api.github.com/repos/.../tarball/v1.0.8"
}
```

**Version Comparison:**
```python
current = "v1.0.7"
latest = "v1.0.8"

# Parse semantic version (strip 'v' prefix)
def parse_version(v):
    return tuple(map(int, v.lstrip('v').split('.')))

if parse_version(latest) > parse_version(current):
    # Download and install update
```

**Download & Install:**
1. Download tarball (5 min timeout)
2. Extract to temp directory
3. Validate critical files exist (main.py, config_fetcher.py, etc.)
4. Backup current installation
5. Copy new files to AIflow/
6. Preserve agent data folders and .service_env
7. Update version file
8. On failure: restore backup

---

### NFC Tag Library (Hot-Swappable)

**Endpoint:**
```
GET https://raw.githubusercontent.com/CollaboratorFuturity/futuresGarden/main/nfc_tags.json
```

**Format:**
```json
{
  "04:A1:B2:C3": "What is the weather today?",
  "04:D4:E5:F6": "Tell me a joke",
  "TEST": "TEST",
  "AGENT_START": "AGENT_START"
}
```

**Reload Trigger:**
- On NFC reader initialization
- On TEST tag detection (hot reload)
- Fallback to local file: `/home/orb/AIflow/{AGENT_ID}/nfc_tags.json`

---

## Service Architecture

### Boot Sequence

```
Raspberry Pi Boot
  ↓
systemd init
  ↓
┌─────────────────────────────────────────┐
│ battery_log.service                     │
│ - Independent monitoring                │
│ - Starts immediately                    │
│ - Runs continuously                     │
└─────────────────────────────────────────┘
  ↓
┌─────────────────────────────────────────┐
│ config_fetcher.service                  │
│ After=network-online.target             │
│ Wants=network-online.target             │
└─────────────────────────────────────────┘
  ↓
Wait for network (60s timeout)
  ↓
Fetch config from Supabase API
  ↓
Check for OTA updates
  ↓
[If update available]
  ├─ Download
  ├─ Validate
  ├─ Backup
  ├─ Install
  └─ Update version
  ↓
Apply WiFi credentials
  ↓
Set system volume
  ↓
Write /tmp/aiflow.env
  ↓
os.execv → main.py
  ↓
┌─────────────────────────────────────────┐
│ main.py (running as config_fetcher PID) │
│ - Load .env from /tmp/aiflow.env        │
│ - Initialize hardware (GPIO, NFC, I2C)  │
│ - Play startup test audio               │
│ - Enter splash_idle state               │
└─────────────────────────────────────────┘
```

### Service Hierarchy Diagram

#### Standard Mode (Cloud-Based)

```
┌────────────────────────────────────────────────────────┐
│                    systemd                              │
└────────────────────────────────────────────────────────┘
           │                           │
           │                           │
           ▼                           ▼
┌──────────────────────┐    ┌──────────────────────────┐
│ battery_log.service  │    │ config_fetcher.service   │
│                      │    │                          │
│ /home/orb/env/bin/   │    │ /home/orb/env/bin/       │
│ python battery_log.py│    │ python config_fetcher.py │
│                      │    │                          │
│ [Independent]        │    │ [Orchestrator]           │
└──────────────────────┘    └──────────────────────────┘
                                       │
                                       │ os.execv
                                       ▼
                            ┌──────────────────────────┐
                            │ main.py                  │
                            │                          │
                            │ (Same PID, new process)  │
                            │                          │
                            │ [Voice Agent]            │
                            └──────────────────────────┘
```

#### Static Mode (No Cloud Dependency)

```
┌────────────────────────────────────────────────────────┐
│                    systemd                              │
└────────────────────────────────────────────────────────┘
           │                           │
           │                           │
           ▼                           ▼
┌──────────────────────┐    ┌──────────────────────────┐
│ battery_log.service  │    │ static_launcher.service  │
│                      │    │                          │
│ /home/orb/env/bin/   │    │ static_launcher.sh       │
│ python battery_log.py│    │                          │
│                      │    │ - Creates /tmp/aiflow.env│
│ [Independent]        │    │ - Sets volume            │
└──────────────────────┘    │ - Launches main.py       │
                            │                          │
                            │ [Static Config]          │
                            └──────────────────────────┘
                                       │
                                       │ exec
                                       ▼
                            ┌──────────────────────────┐
                            │ main.py                  │
                            │                          │
                            │ (Hardcoded agent/volume) │
                            │ (AUTO_START=true)        │
                            │                          │
                            │ [Voice Agent]            │
                            └──────────────────────────┘
```

### Thread Architecture (main.py runtime)

```
┌────────────────────────────────────────────────────────┐
│              Main Process (main.py)                     │
└────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┬─────────────┐
        │               │               │             │
        ▼               ▼               ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌─────────┐ ┌──────────┐
│ Main Thread  │ │Button Thread │ │NFC Thread│ │Shared    │
│              │ │              │ │          │ │Resources │
│ asyncio loop │ │ GPIO poll    │ │I2C poll  │ │          │
│ - WebSocket  │ │ 10ms rate    │ │200ms TO  │ │- Globals │
│ - audio I/O  │ │              │ │          │ │- Locks   │
│ - pong_task  │ │ Callbacks:   │ │Callbacks:│ │- Events  │
│ - recv loop  │ │ - set_idle   │ │- inject  │ │          │
└──────────────┘ └──────────────┘ └─────────┘ └──────────┘
```

### Battery Monitor (Independent Process)

```
┌────────────────────────────────────────────────────────┐
│          battery_log.py Process                         │
└────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│Monitor Thread│ │Upload Thread │ │ Shared Queue │
│              │ │              │ │              │
│ INA219 read  │ │ HTTP POST    │ │ Persistent   │
│ 30s interval │ │ 90s interval │ │ JSON file    │
│              │ │              │ │              │
│ Voltage check│ │ Batch upload │ │/tmp/battery_ │
│ Shutdown     │ │ Retry logic  │ │queue.json    │
│ detection    │ │              │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

## Data Flow

### User Turn (PTT Mode)

```
[1] User presses button
      ↓
[2] mute_button.py detects GPIO change (10ms poll)
      ↓
[3] main.py: stream_audio_ptt() → Cancel pong_task
      ↓
[4] 150ms stabilization delay
      ↓
[5] serial_com.write('U') → Display shows unmuted animation
      ↓
[6] setup_mic() → ALSA capture device
      ↓
[7] Loop: Read 30ms frames (960 bytes)
      ↓
[8] Base64 encode
      ↓
[9] WebSocket send: {"user_audio_chunk": base64_pcm}
      ↓
[10] User releases button
      ↓
[11] Send 50 frames of silence (1.5s)
      ↓
[12] stream_audio_ptt() returns
```

### Agent Turn

```
[1] stream_audio() completes (pong_task already cancelled)
      ↓
[2] main.py: receive_response() called
      ↓
[3] serial_com.write('L') → Display shows loading animation
      ↓
[4] setup_speaker() → ALSA playback device
      ↓
[5] Loop: ws.recv() with 0.1s timeout
      ↓
[6] Receive message type "audio"
      ↓
[7] Base64 decode PCM data
      ↓
[8] Append to out_buf
      ↓
[9] drain(out_buf) → Write full frames to speaker
      ↓
[10] First audio chunk triggers:
       - serial_com.write('O') → Display shows agent speaking
       - Log first chunk size
      ↓
[11] Continue until no content for CONTENT_IDLE seconds
      ↓
[12] grace_drain() → Check for straggler messages
      ↓
[13] receive_response() returns
      ↓
[14] Restart pong_task for next turn
```

### NFC Tag Detection

```
[1] nfc_backend.py thread polls PN532 (I2C)
      ↓
[2] Tag detected, read UID (XX:XX:XX format)
      ↓
[3] Lookup UID in nfc_tags.json
      ↓
[4] Check debounce (1.5s since last read)
      ↓
[5] Determine tag type:
      │
      ├─ "TEST" → Call on_nfc_tag_detected("TEST")
      │            ↓
      │          hot_reload_config()
      │            ↓
      │          Fetch config, update agent/volume/mode, play test audio
      │
      ├─ "AGENT_START" → Call on_nfc_tag_detected("AGENT_START")
      │                    ↓
      │                  set_state("running_agent")
      │                    ↓
      │                  Connect WebSocket, start conversation
      │
      └─ Custom phrase → Queue phrase for injection
                          ↓
                        asyncio.run_coroutine_threadsafe(
                          ws.send({"type": "user_message", "text": phrase})
                        )
                          ↓
                        Agent receives as user input
```

### OTA Update Flow

```
[1] config_fetcher.py: check_and_apply_updates()
      ↓
[2] HTTP GET: GitHub releases API
      ↓
[3] Compare tag_name with current version
      ↓
[4] [If newer version available]
      ↓
[5] Download tarball (5 min timeout)
      ↓
[6] Extract to temp directory
      ↓
[7] Validate critical files:
      - main.py, config_fetcher.py, battery_log.py
      - serial_com.py, mute_button.py, nfc_backend.py
      - INA219.py, version
      ↓
[8] [If validation fails]
      └─ Clean up temp, abort update
      ↓
[9] Backup current installation:
      cp -r AIflow/ AIflow.backup/
      ↓
[10] Remount filesystem RW (if read-only):
       rwro rw
      ↓
[11] Copy new files to AIflow/
      ↓
[12] Preserve:
       - Agent data folders (*/test.wav, */nfc_tags.json)
       - beep.wav
       - .service_env
      ↓
[13] Update version file
      ↓
[14] Remount filesystem RO (if was read-only):
       rwro ro
      ↓
[15] [If any error]
       └─ Restore from backup
      ↓
[16] Continue to main.py via os.execv
      ↓
[17] main.py loads with new code (zero downtime)
```

### Hot Reload (TEST Tag)

```
[1] NFC TEST tag detected
      ↓
[2] on_nfc_tag_detected("TEST") called
      ↓
[3] hot_reload_config() function
      ↓
[4] serial_com.write('L') → Show loading
      ↓
[5] HTTP GET: Supabase config API (2-3s)
      ↓
[6] Parse response:
      - agent_name → map to AGENT_ID
      - volume → apply via amixer
      - input_mode → update env
      ↓
[7] Write /tmp/aiflow.env (overwrite)
      ↓
[8] Reload environment: dotenv.load_dotenv(override=True)
      ↓
[9] Update globals: AGENT_ID, INPUT_MODE, WS_ENDPOINT
      ↓
[10] Update mute_button mode: set_mode(INPUT_MODE)
      ↓
[11] Play test audio:
       /home/orb/AIflow/{AGENT_ID}/test.wav
      ↓
[12] serial_com.write('S') → Return to splash idle
      ↓
[13] hot_reload_config() returns
      ↓
[14] Device ready with new config (NO process restart)
```

---

## License & Credits

**Project:** The Orb - Voice Agent System
**Version:** v1.0.7
**Platform:** Raspberry Pi Zero W 1.1
**Organization:** Futurity Engineering

**Key Technologies:**
- ElevenLabs Conversational AI
- Raspberry Pi OS (Debian)
- Python 3.9+ with asyncio
- ALSA (Advanced Linux Sound Architecture)
- WebRTC VAD
- PN532 NFC (Adafruit)
- INA219 Power Monitor

**External Libraries:**
- websockets (WebSocket client)
- requests (HTTP client)
- pyalsaaudio (ALSA bindings)
- webrtcvad (Voice Activity Detection)
- adafruit-circuitpython-pn532 (NFC reader)
- smbus (I2C communication)

**Documentation Authors:**
- System Architecture: Futurity Engineering
- Technical Reference: Claude (Anthropic)

**Last Updated:** 2025-01-15
