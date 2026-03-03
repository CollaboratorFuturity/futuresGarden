# Orb Firmware — Known Issues

## Bugs

### 1. `stop_mute_button()` is truncated / incomplete
**File:** `mute_button.py:183-186`

The function body just sets `__STOP` and ends. It never joins the thread, never deinits the GPIO pin, and never resets `__THREAD`/`__BTN` to `None`. Looks like the rest of the function was accidentally deleted when `force_turn_end` was added directly below it.

```python
def stop_mute_button():
    """Stop the watcher and deinit the pin. Safe to call multiple times."""
    global __THREAD, __BTN
    __STOP.set()
# Global event to force turn end (for NFC, etc.)   ← immediately after, no body
force_turn_end = threading.Event()
```

**Impact:** GPIO pin is never released. Thread is never joined. `_shutdown()` in main.py calls this and assumes cleanup happened, but it didn't.

---

### 2. VOLUME_MAP mismatch between boot and hot reload
**Files:** `config_fetcher.py:227-238` (boot) vs `main.py:464-475` (hot reload)

The volume lookup tables have different raw ALSA values for the same level:

| Level | config_fetcher (boot) | main.py (hot reload) |
|-------|---|---|
| 9 | 120 | 121 |
| 8 | 117 | 118 |
| 7 | 113 | 114 |
| 6 | 108 | 110 |
| 5 | 103 | 104 |
| 4 | 90 | 96 |
| 3 | 80 | 85 |
| 2 | 70 | 65 |

**Impact:** Volume set at boot differs from volume after a TEST-tag hot reload, even with the same config value. User hears a volume jump on hot reload.

---

### 3. `did_init=True` set even on greeting failure
**File:** `main.py:1234-1235`

```python
except Exception:
    did_init=True  # greeting failed, but we mark it as done anyway
```

If the first-connection greeting throws (network glitch, ALSA error), `did_init` is set to `True`. All subsequent reconnections within this session use `SUPPRESS_GREETING`, so the user never hears the agent's voice. The conversation starts silently.

---

## Race Conditions

### 4. ALSA speaker opened from NFC thread during playback
**File:** `main.py:258-290`

`play_beep()` is called from `on_nfc_tag_detected()`, which runs in the NFC thread (via `tag_callback`). It calls `setup_speaker()` which opens an ALSA PCM on `plughw:0,0`. If the user scans a tag while the agent is speaking, `receive_response()` already has a speaker open on the same device.

Two PCM playback handles on the same ALSA device = audio corruption or an exception.

Same issue applies to `hot_reload_config` — it calls `play_agent_greeting()` which also opens a speaker, and can run while `receive_response` still has its speaker open.

---

### 5. `hot_reload_config` + `receive_response` both hold speakers and WebSockets
**File:** `main.py:966-1168` (receive_response), `main.py:477-629` (hot_reload_config)

`receive_response()` checks `STOP` but never checks `get_state()`. When `hot_reload_config()` sets state to `splash_idle`, `receive_response` keeps playing audio until its idle/timeout logic triggers naturally (up to 60s absolute timeout). Meanwhile `hot_reload_config` opens a new speaker and WebSocket for the greeting — two speakers and two WebSocket connections simultaneously.

---

### 6. Stale `force_turn_end` event causes phantom `receive_response`
**Files:** `main.py` (stream_audio_ptt, run_session), `mute_button.py`

`force_turn_end` can be set during `receive_response()` (NFC tag scanned while agent speaks), but `receive_response` never checks or clears it. The event persists until the next `stream_audio_ptt()` call, which immediately detects it and exits, setting `NFC_TRIGGERED_TURN`. This triggers another `receive_response()` call that waits for a response that may never come — hitting the 15-second `FIRST_CONTENT_MAX` timeout.

---

## Dead Ends

### 7. `AGENT_START` during `running_agent` is a no-op
**File:** `main.py:315-316`

If the conversation is stuck (WebSocket dead, reconnect backoff), scanning `AGENT_START` again does nothing:

```python
elif tag_name == "AGENT_START":
    set_state("running_agent")  # already running_agent → no-op
```

No way for the user to force-restart a stale session. The reconnect backoff loop continues for up to 10 seconds per cycle with no user recourse.

---

### 8. NFC init failure = device stuck in splash forever
**File:** `nfc_backend.py:172-229`

If PN532 init fails after 5 attempts, the NFC thread dies silently with `return`. The Orb sits in `splash_idle` forever with no user-visible feedback that NFC is broken. No fallback, no timeout, no retry-later logic. The LED just shows the splash animation indefinitely.

---

## Minor Issues

### 9. Two processes sharing serial port without IPC synchronization
**Files:** `main.py`, `battery_log.py`, `serial_com.py`

`main.py` and `battery_log.py` run as separate systemd services, each importing `serial_com` and opening `/dev/ttyUSB0` independently. The `_fd_lock` in `serial_com.py` only synchronizes within a single process. LED state can flicker if both processes send conflicting commands at the same moment.

---

### 10. Duplicate keys in `nfc_tags.json`
**File:** `nfc_tags.json`

Keys like `"****NOVA****"` are used as section separators but appear multiple times. JSON doesn't support duplicate keys — Python's `json.load` silently keeps only the last value. These "comment" keys don't match any real NFC UID so they're functionally harmless, but if a real tag UID were accidentally duplicated it would silently use only the last mapping.

---

### 11. Hardcoded agent ID for Nova volume boost
**Files:** `main.py:348`, `main.py:1183`

```python
if AGENT_ID == "agent_1701k5bgdzmte5f9q518mge3jsf0":
    tts_config["volume"] = 5
```

Appears in two places and will silently stop working if the Nova agent ID ever changes. Should come from the config API (e.g., a `tts_volume` field).
