# Changelog

All notable changes to The Orb voice agent system will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.7] - 2025-01-15

### Fixed

#### Critical: Mid-Sentence Audio Loss (Race Condition)
- **Issue:** Agent responses sometimes started mid-sentence, with the beginning cut off
- **Root Cause:** `maintain_pong()` task was calling `ws.recv()` concurrently with `receive_response()`, consuming and discarding audio messages before they could be processed
- **Impact:** First audio chunk (typically 2-4 seconds) would be lost, causing playback to start partway through the response
- **Fix:** Cancel `pong_task` immediately when user turn STARTS (button press or speech detection), ensuring `receive_response()` has exclusive ownership of WebSocket
  - Modified `stream_audio_ptt()`: Added pong_task cancellation on button press ([main.py:717-727](main.py#L717-L727))
  - Modified `stream_audio_vad()`: Added pong_task cancellation on speech detection ([main.py:857-867](main.py#L857-L867))
  - Updated `stream_audio()` signature to accept `pong_task` parameter ([main.py:639](main.py#L639))
  - Updated call site to pass `pong_task` to `stream_audio()` ([main.py:1150](main.py#L1150))
  - Removed redundant cancellation logic after `stream_audio()` returns ([main.py:1180-1181](main.py#L1180-L1181))
- **Result:** Complete audio responses from beginning to end, no more mid-sentence starts

#### Dropped Conversations (Timeout Too Aggressive)
- **Issue:** Some agent responses were timing out with no audio/content received, despite ElevenLabs successfully generating responses
- **Root Cause:** `FIRST_CONTENT_MAX` timeout was set to 5.0 seconds, which was insufficient for complex responses requiring longer processing time
- **Impact:** User would ask a question, see "saw_content=False, saw_audio=False" in logs, and receive no response
- **Fix:** Increased `FIRST_CONTENT_MAX` from 5.0 to 15.0 seconds ([main.py:67](main.py#L67))
- **Rationale:** ElevenLabs can take 10+ seconds to generate complex responses, especially for long-form answers or reasoning tasks
- **Result:** No more timeout-related dropped conversations

### Changed

#### Logging Enhancements
- Enabled detailed logging (`detail = True`) for debugging audio timing issues ([main.py:39](main.py#L39))
- Added comprehensive WebSocket message type logging (all non-ping messages)
- Added first audio chunk size logging with buffer state
- Added audio buffer growth warnings (when buffer exceeds 10 frames / 300ms)
- Added detailed pong_task cancellation logging in both PTT and VAD modes

#### Documentation
- Added inline comments explaining pong_task cancellation timing and rationale
- Updated docstrings for `stream_audio()`, `stream_audio_ptt()`, and `stream_audio_vad()` to document `pong_task` parameter

### Technical Details

#### Architecture Changes
- **Process Flow:** pong_task lifecycle now strictly managed per conversation turn
  - Started: After agent response completes ([main.py:1188](main.py#L1188))
  - Cancelled: When user turn starts (button press or speech detection)
  - Never running: During user audio streaming or agent response reception

#### Performance Impact
- Negligible CPU impact (one fewer coroutine running during user turn)
- Reduced potential for WebSocket message queue buildup
- Improved responsiveness (no pong_task overhead during active conversation)

#### Testing Notes
- Tested with complex multi-sentence agent responses (3+ sentences)
- Verified complete audio playback from first syllable to last
- Confirmed no timeouts with 15-second window for long-form responses
- Validated turn boundaries with 1.5s silence (END_SILENCE_CHUNKS = 50 frames)

---

## [1.0.6] - 2025-01-14

### Added
- Auto-updater system fully operational
- Version tracking in `version` file
- GitHub Releases integration for OTA updates
- Backup and restore mechanism for failed updates

### Fixed
- Update validation (checks for critical files before applying)
- Preserved agent data folders during updates
- Read-only filesystem compatibility (rwro tool integration)

### Changed
- Switched to GitHub-based distribution model
- Process replacement via `os.execv()` for zero-downtime updates

---

## [1.0.5] - 2025-01-13

### Added
- NFC hot-swappable tag library (GitHub-hosted nfc_tags.json)
- TEST tag for hot reload (2-3s config refresh without restart)
- Agent name mapping (Zane, Rowan, Nova, Cypher)

### Fixed
- NFC debouncing (1.5s same-UID ignore)
- Tag library fallback to local file on network failure

### Changed
- NFC tag URL now configurable (GitHub raw URL)
- Reload tags on NFC reader initialization and TEST tag detection

---

## [1.0.4] - 2025-01-12

### Added
- Battery monitoring service (battery_log.py)
- INA219 voltage/current sensor integration
- Three-stage shutdown protection:
  1. Pi under-voltage detection (immediate)
  2. Critical voltage (3.55V, graceful shutdown)
  3. Low voltage warning (3.65V, display icon)
- Telemetry upload to Supabase (90s interval)
- Persistent upload queue (/tmp/battery_queue.json)

### Fixed
- Safe shutdown with display animation ('D' locked)
- Dual-averaged voltage readings (±0.02V accuracy)

### Changed
- Battery monitoring now independent systemd service
- Separated from main application (cannot be taken down by app crashes)

---

## [1.0.3] - 2025-01-11

### Added
- WiFi provisioning via NetworkManager (nmcli)
- Cloud-based configuration (Supabase API)
- Volume control (1-10 scale with calibrated ALSA mapping)
- Device name customization

### Fixed
- WiFi connection persistence (remount /etc/NetworkManager as RW)
- Fallback WiFi profile creation

### Changed
- Startup sequence now fetches config before launching main.py
- Environment variables generated at runtime (/tmp/aiflow.env)

---

## [1.0.2] - 2025-01-10

### Added
- State machine (splash_idle, running_agent)
- AGENT_START NFC tag to trigger conversation
- Splash screen display ('S') when idle
- State-aware button behavior (only active in running_agent)

### Fixed
- Button responsiveness (state check callback)
- Display animation synchronization

### Changed
- Main application now waits for NFC tag before connecting WebSocket
- Reduced unnecessary WebSocket connections

---

## [1.0.1] - 2025-01-09

### Added
- NFC tag detection and phrase injection
- PN532 I2C reader integration
- Background NFC thread with retry logic
- asyncio integration (run_coroutine_threadsafe)

### Fixed
- NFC chip validation (firmware version check)
- I2C communication stability

### Changed
- NFC scanning can be enabled/disabled per turn
- Phrase queue with bounded size (maxlen=16)

---

## [1.0.0] - 2025-01-08

### Added
- Initial production release
- Dual input modes: PTT (Push-to-Talk) and VAD (Voice Activity Detection)
- ElevenLabs WebSocket streaming integration
- Real-time audio capture and playback (16kHz mono PCM)
- ALSA audio interface (pyalsaaudio)
- WebRTC VAD for speech detection
- GPIO button handler with mode-aware behavior
- Serial display communication (single-byte commands)
- Turn-taking coordination (user turn ↔ agent turn)
- Ping/pong keepalive mechanism
- Graceful shutdown (SIGTERM/SIGINT handling)
- Audio device cleanup (safe_close_all)
- TurnMetrics tracking
- Short-turn filtering (<800ms audio rejected)

### Technical Details
- Python 3.9+ with asyncio
- WebSocket library: websockets
- Audio: 16kHz, mono, S16_LE, 30ms frames (480 samples)
- VAD parameters: Mode 3 (aggressive), 600ms min speech, 1500ms silence to end
- PTT: 1s minimum button press, 150ms stabilization delay
- Button: GPIO12 (D12), active-low, 50ms debounce
- Display: Serial UART (115200 baud, 8N1)

---

## [0.9.0] - 2025-01-05

### Added
- Prototype version
- Basic WebSocket connection to ElevenLabs
- ALSA audio capture
- Simple audio playback

### Known Issues
- No state machine (always connected)
- No NFC support
- No battery monitoring
- No OTA updates
- No configuration management

---

## Version History Summary

| Version | Date       | Key Feature                              |
|---------|------------|------------------------------------------|
| 1.0.7   | 2025-01-15 | Fixed mid-sentence audio loss (pong race)|
| 1.0.6   | 2025-01-14 | Auto-updater operational                 |
| 1.0.5   | 2025-01-13 | NFC hot reload (TEST tag)                |
| 1.0.4   | 2025-01-12 | Battery monitoring service               |
| 1.0.3   | 2025-01-11 | WiFi provisioning + cloud config         |
| 1.0.2   | 2025-01-10 | State machine + NFC trigger              |
| 1.0.1   | 2025-01-09 | NFC phrase injection                     |
| 1.0.0   | 2025-01-08 | Initial production release               |
| 0.9.0   | 2025-01-05 | Prototype                                |

---

## Upgrade Notes

### Upgrading to 1.0.7 from 1.0.6

**No manual steps required.** OTA update will apply automatically on next boot.

**Expected changes:**
- More verbose logging (detail = True)
- Slightly faster response times (pong_task cancelled earlier)
- Complete audio responses (no more mid-sentence starts)
- Longer timeout tolerance (15s vs 5s for first content)

**Rollback instructions:**
If issues occur, revert to v1.0.6:
```bash
cd /home/orb
sudo rm -rf AIflow/
sudo mv AIflow.backup/ AIflow/
sudo systemctl restart config_fetcher.service
```

### Upgrading to 1.0.6 from 1.0.5

**OTA update system now operational.** Future updates will apply automatically.

**Manual steps (if needed):**
```bash
# Check current version
cat /home/orb/AIflow/version

# If not v1.0.6, manually update:
cd /home/orb
wget https://github.com/CollaboratorFuturity/futuresGarden/releases/download/v1.0.6/AIflow.tar.gz
tar -xzf AIflow.tar.gz
sudo systemctl restart config_fetcher.service
```

### Upgrading to 1.0.5 from 1.0.4

**NFC tag library now hot-swappable.** Update tags without device restart.

**Migration steps:**
1. Ensure TEST tag is in your NFC tag collection
2. Scan TEST tag to trigger hot reload
3. Verify agent name mapping in cloud config

### Upgrading to 1.0.4 from 1.0.3

**Battery monitoring now required.** Install battery_log.service.

**Installation:**
```bash
cd /home/orb/AIflow
sudo ./install_services.sh
sudo systemctl enable battery_log.service
sudo systemctl start battery_log.service
```

**Hardware requirements:**
- INA219 sensor on I2C bus (address 0x43)
- LiPo battery connected to INA219

### Upgrading to 1.0.3 from 1.0.2

**Cloud configuration now mandatory.** Device will not start without valid config.

**Prerequisites:**
- DEVICE_ID set in .service_env
- Device registered in Supabase
- LOVABLE_API_KEY configured

**Migration:**
```bash
# Add to .service_env:
echo "DEVICE_ID=your_device_id" >> /home/orb/AIflow/.service_env
echo "LOVABLE_API_KEY=your_api_key" >> /home/orb/AIflow/.service_env

sudo systemctl daemon-reload
sudo systemctl restart config_fetcher.service
```

---

## Known Issues

### v1.0.7
- Display glitch during speaker playback (voltage drop, hardware issue)
- No acoustic echo cancellation (speaker audio picked up by microphone)
- Fixed audio format (16kHz only, no higher quality options)

### All Versions
- Single WebSocket connection (no concurrent conversations)
- No offline mode (requires internet connectivity)
- No multi-user support (no speaker identification)

---

## Roadmap

### v1.1.0 (Planned)
- [ ] Automatic process restart on memory threshold
- [ ] Configurable sample rates (24kHz, 48kHz)
- [ ] Improved error recovery (WebSocket reconnect strategies)
- [ ] Power saving mode (CPU frequency scaling during idle)

### v1.2.0 (Planned)
- [ ] Acoustic Echo Cancellation (AEC)
- [ ] Hardware AEC-enabled audio interface support
- [ ] Noise suppression (RNNoise integration)
- [ ] Multi-channel audio (stereo output)

### v2.0.0 (Future)
- [ ] Offline mode with local TTS/ASR
- [ ] Multi-user support (voice biometrics)
- [ ] Web UI for device configuration
- [ ] Mobile app for setup and control
- [ ] Real-time monitoring dashboard

---

## Contributing

When contributing to this project, please:
1. Update the version number in `version` file (follow semver)
2. Add an entry to this CHANGELOG under "Unreleased" section
3. Include detailed description of changes, fixes, and impacts
4. Update README.md if user-facing features changed
5. Update ARCHITECTURE.md if system design changed

### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code style changes (formatting, no logic change)
- `refactor`: Code refactoring (no feature or bug fix)
- `perf`: Performance improvement
- `test`: Adding or updating tests
- `chore`: Build process, tooling, dependencies

**Example:**
```
fix(audio): Cancel pong_task before user turn to prevent audio loss

The maintain_pong() task was consuming audio messages before
receive_response() could process them, causing the first 2-4
seconds of agent audio to be discarded.

Modified stream_audio_ptt() and stream_audio_vad() to cancel
pong_task immediately when user starts speaking (button press
or speech detection).

Fixes #42
```

---

## Contact

**Project:** The Orb - Voice Agent System
**Organization:** Futurity Engineering
**Repository:** https://github.com/CollaboratorFuturity/futuresGarden

**For issues or questions:**
- Open an issue on GitHub
- Email: support@futurityengineering.com

---

**Changelog Version:** 1.0
**Last Updated:** 2025-01-15
