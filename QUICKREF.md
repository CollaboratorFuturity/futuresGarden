# Orb Quick Reference

## Module Map

```
config_fetcher.py  ──exec──>  main.py
(boot orchestrator)           (core app)
                                ├── mute_button.py  (GPIO12 button)
                                ├── nfc_backend.py   (PN532 I2C tags)
                                ├── serial_com.py    (LED UART commands)
                                └── constants.py     (shared config)

battery_log.py  (separate process)
├── INA219.py        (voltage/current sensor)
└── serial_com.py    (LED UART commands)
```

## Boot Sequence

```
[systemd] battery_log.service
              └─ battery_log.py (runs independently, monitors voltage)

[systemd] config_fetcher.service
              └─ config_fetcher.py
                    1. Wait for network (TCP socket to 8.8.8.8:53, no TLS)
                    2. Fetch config from Supabase (agent_id, volume, input_mode, wifi)
                    3. Write /tmp/aiflow.env
                    4. Set ALSA volume
                    5. Skip WiFi if already connected to SSID, else configure
                    6. Check GitHub for OTA updates
                    7. os.execv() → main.py  (replaces process)

              └─ main.py (now running as PID of config_fetcher)
                    1. Play agent greeting via ElevenLabs WebSocket
                    2. Enter splash_idle → serial 'S'
                    3. Wait for NFC tag scan
```

## State Machine

```
                  AGENT_START tag
  [splash_idle] ─────────────────> [running_agent]
       ^                                  │
       │     TEST tag or AGENT_START      │
       └──────── (hot reload) ────────────┘
```

- **splash_idle**: LED shows splash (`S`). NFC scanning. Waiting for AGENT_START.
- **running_agent**: Active ElevenLabs conversation. Turn loop: mic → agent → mic → ...

## NFC Tag Types

| Tag phrase     | Action                                              |
|----------------|-----------------------------------------------------|
| `AGENT_START`  | From splash: start session. During session: hot reload + splash. |
| `TEST`         | Hot reload config from Supabase + play greeting + splash. |
| Any other      | Send phrase as text message to agent via WebSocket.  |

## Serial Protocol (LED Commands)

| Char | Meaning         | Sent when                        |
|------|-----------------|----------------------------------|
| `S`  | Splash idle     | Waiting for NFC scan             |
| `L`  | Loading         | Processing / connecting          |
| `O`  | Agent speaking  | First audio chunk received       |
| `M`  | Muted (PTT)     | Waiting for button press         |
| `U`  | Unmuted         | Button held / VAD listening      |
| `N`  | NFC animation   | Regular NFC tag scanned          |
| `B`  | Boot/shutdown   | Process starting or exiting      |
| `D`  | Dying           | Critical battery shutdown        |
| `V`  | Battery icon    | Sustained low voltage warning    |

## Audio Config

| Parameter          | Value   |
|--------------------|---------|
| Sample rate        | 16 kHz  |
| Channels           | 1 (mono)|
| Format             | S16_LE  |
| Frame              | 30 ms (480 samples, 960 bytes) |
| VAD mode           | 3 (most aggressive) |
| PTT min press      | 1000 ms |
| Silence end (VAD)  | 1500 ms |

## Key Paths (on device)

| Path                            | Purpose                            |
|---------------------------------|------------------------------------|
| `/home/orb/AIflow/`             | Code directory (read-only)         |
| `/home/orb/AIflow/.service_env` | Persistent secrets (DEVICE_ID, API keys) |
| `/home/orb/AIflow/version`      | Installed version tag              |
| `/home/orb/AIflow/beep.wav`     | NFC scan feedback sound            |
| `/home/orb/AIflow/nfc_tags.json`| NFC UID-to-phrase mappings         |
| `/tmp/aiflow.env`               | Runtime config (tmpfs, regenerated on boot) |
| `/tmp/config_fetcher.log`       | Boot + runtime log (tmpfs)         |
| `/tmp/battery_shutdown`         | Shutdown flag file                 |

## External APIs

| Service      | Endpoint                                              | Purpose           |
|--------------|-------------------------------------------------------|--------------------|
| ElevenLabs   | `wss://api.elevenlabs.io/v1/convai/conversation`      | Voice agent WebSocket |
| Supabase     | `get-device-config?device_id=...`                     | Fetch device config |
| Supabase     | `update-battery`                                      | Upload telemetry   |
| GitHub       | `api.github.com/repos/.../releases/latest`            | OTA update check   |

## Useful Commands

```bash
# Logs
sudo journalctl -u battery_log.service -f
sudo journalctl -u config_fetcher.service -f
tail -f /tmp/config_fetcher.log

# Restart
sudo systemctl restart config_fetcher.service

# Status
sudo systemctl status battery_log.service
sudo systemctl status config_fetcher.service
bash /home/orb/AIflow/check_services.sh
```
