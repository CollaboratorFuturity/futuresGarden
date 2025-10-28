import os
import json
import time
import threading
import asyncio
from collections import deque
logNFC = False
# Lazy-import HW libs so this file won’t crash if the board/lib isn’t present
# until the NFC thread actually starts.
def _lazy_hw():
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C
    return board, busio, PN532_I2C

def _uid_to_str(uid_bytes) -> str:
    # uid is like b'\x04\xA2...'; normalize to "04:A2:..."
    if uid_bytes is None:
        return ""
    if isinstance(uid_bytes, (bytes, bytearray)):
        return ":".join(f"{b:02X}" for b in uid_bytes)
    # some drivers may return list[int]
    return ":".join(f"{int(b)&0xFF:02X}" for b in uid_bytes)

class NfcReader:
    """
    Background NFC loop:
      - Reads PN532 (I2C)
      - Debounces same UID for debounce_s
      - Looks up UID in <base_dir>/<agent_id>/nfc_tags.json
      - Sends phrase to ElevenLabs over the active websocket via asyncio.run_coroutine_threadsafe
    """
    def __init__(self, agent_id: str, base_dir: str, debounce_s: float = 1.5, log=print, tags_url: str = None, tag_callback=None):
        self.agent_id = agent_id or "default"
        self.base_dir = base_dir
        self.debounce_s = debounce_s
        self.log = log
        self.tags_url = tags_url
        self.tag_callback = tag_callback  # Callback for special tag phrases (TEST, AGENT_START)

        self._stop = threading.Event()
        self._thr = None

        # Enable/disable scanning per turn
        self.enabled = True
        self._enabled_lock = threading.Lock()

        # Sender state (set by main on connect)
        self._ws = None
        self._loop = None
        self._lock = threading.Lock()

        # Debounce & buffering
        self._last_uid = None
        self._last_when = 0.0
        self._pending = deque(maxlen=16)

        # Tag map
        self._tags = {}
        self._load_tags()

    # ---------------- Tag map ----------------
    def _tags_path(self) -> str:
        return os.path.join(self.base_dir, self.agent_id, "nfc_tags.json")

    def _load_tags(self):
        import json
        data = None
        if self.tags_url:
            try:
                import urllib.request
                with urllib.request.urlopen(self.tags_url, timeout=5) as resp:
                    data = json.load(resp)
                if logNFC: self.log(f"🌐 NFC tags loaded from URL: {self.tags_url}")
            except Exception as e:
                self.log(f"⚠️ NFC tags fetch error from {self.tags_url}: {e}")
        if data is None:
            path = self._tags_path()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if logNFC:self.log(f"📚 NFC tags loaded: {len(data) if data else 0} from {path}")
            except FileNotFoundError:
                self._tags = {}
                self.log(f"⚠️ NFC tags file not found: {path} (no-op until present)")
                return
            except Exception as e:
                self._tags = {}
                self.log(f"⚠️ NFC tags load error from {path}: {e}")
                return
        # Accept list of pairs or dict {uid: phrase}
        if isinstance(data, dict):
            self._tags = {k.strip().upper(): str(v) for k, v in data.items()}
        else:
            # e.g. [["FF:FF:...", "phrase"], ...]
            m = {}
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    k = str(item[0]).strip().upper()
                    m[k] = str(item[1])
            self._tags = m

    def reload_tags(self):
        """Call this if you updated the JSON on disk."""
        self._load_tags()

    # -------------- Sender wiring --------------
    def set_sender(self, ws, loop):
        """Provide the active websocket + loop on each (re)connect."""
        with self._lock:
            self._ws = ws
            self._loop = loop
        # Try to flush queued phrases if any
        self._flush_pending()

    def _flush_pending(self):
        while True:
            with self._lock:
                ws = self._ws
                loop = self._loop
            if not (ws and loop):
                return
            try:
                phrase = self._pending.popleft()
            except IndexError:
                return
            self._send_to_ws(ws, loop, phrase)

    def _send_to_ws(self, ws, loop, phrase: str):
        async def _go():
            try:
                # Light nudge (optional, but helps keep stream “alive”)
                await ws.send(json.dumps({"type": "user_activity"}))
            except Exception:
                pass
            await ws.send(json.dumps({"type": "user_message", "text": phrase}))
        try:
            fut = asyncio.run_coroutine_threadsafe(_go(), loop)
            fut.result(timeout=5.0)
            self.log(f"📨 NFC → agent: {phrase}")
        except Exception as e:
            self.log(f"⚠️ NFC send error, queuing phrase: {e}")
            self._pending.appendleft(phrase)  # requeue at front

    # ---------------- Thread control ----------------
    def start(self):
        if self._thr and self._thr.is_alive():
            return
        self._stop.clear()
        self._thr = threading.Thread(target=self._run, name="NfcReader", daemon=True)
        self._thr.start()

    def stop(self):
        self._stop.set()
        if self._thr:
            self._thr.join(timeout=1.5)
        self._thr = None

    # ---------------- Enable/disable scanning ----------------
    def enable(self):
        with self._enabled_lock:
            self.enabled = True
            if logNFC: self.log("[NFC] ENABLED")

    def disable(self):
        with self._enabled_lock:
            self.enabled = False
            if logNFC: self.log("[NFC] DISABLED")

    # ---------------- Main loop ----------------
    def _run(self):
        # Try multiple times to initialize PN532 (can be flaky on startup)
        MAX_RETRIES = 5  # Increased from 3
        pn532 = None
        
        for attempt in range(MAX_RETRIES):
            try:
                board, busio, PN532_I2C = _lazy_hw()
                
                # Longer initial delay for I2C bus stabilization
                time.sleep(0.5 + (attempt * 0.3))  # Exponential backoff
                
                # Create I2C bus (this can fail if previous attempt left bus in bad state)
                try:
                    i2c = busio.I2C(board.SCL, board.SDA)
                except Exception as i2c_err:
                    self.log(f"⚠️ I2C bus creation failed on attempt {attempt+1}: {i2c_err}")
                    time.sleep(1.0)
                    continue
                
                # Give I2C extra time to stabilize
                time.sleep(0.3)
                
                # Initialize PN532
                pn532 = PN532_I2C(i2c, debug=False)
                
                # Critical: SAM configuration can fail if chip not fully ready
                # This is where "Response length checksum" errors often occur
                time.sleep(0.5)  # Increased delay before SAM config
                
                pn532.SAM_configuration()
                
                # Verify chip is responding by trying to read firmware version
                time.sleep(0.2)
                try:
                    ic, ver, rev, support = pn532.firmware_version
                    self.log(f"✅ NFC chip detected: PN5{ic:02X} v{ver}.{rev}")
                except Exception as fw_err:
                    raise Exception(f"Chip detected but not responding correctly: {fw_err}")
                
                # Success!
                if logNFC: self.log("🪪 NFC ready (PN532 I2C). Waiting for tags...")
                break
                
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    self.log(f"⚠️ NFC init attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
                    # Exponential backoff: longer wait after each failure
                    wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s, 4s, 8s
                    self.log(f"   Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    
                    # Try to clean up any stale I2C state
                    pn532 = None
                else:
                    self.log(f"❌ NFC init failed after {MAX_RETRIES} attempts: {e}")
                    self.log(f"   Check I2C connections and permissions (i2c group)")
                    return
        
        if pn532 is None:
            self.log("❌ NFC initialization failed - pn532 object not created")
            return

        self.log("[NFC] Thread running, entering scan loop")
        while not self._stop.is_set():
            # Only scan if enabled
            with self._enabled_lock:
                if not self.enabled:
                    time.sleep(0.1)
                    continue
            try:
                uid = pn532.read_passive_target(timeout=0.2)
            except Exception as e:
                # transient I2C hiccup; brief sleep and continue
                time.sleep(0.2)
                continue

            if uid is None:
                continue

            now = time.time()
            uid_str = _uid_to_str(uid).upper()

            # Debounce identical UID
            if uid_str == self._last_uid and (now - self._last_when) < self.debounce_s:
                continue

            self._last_uid, self._last_when = uid_str, now

            phrase = self._tags.get(uid_str)
            if logNFC: self.log(f"🔎 NFC scan: {uid_str} → {('MATCH' if phrase else 'unmapped')}")

            if not phrase:
                continue

            # Call tag callback for all known tags
            if self.tag_callback:
                try:
                    self.tag_callback(phrase)
                    if logNFC: self.log(f"[NFC] Tag callback invoked for: {phrase}")
                except Exception as e:
                    self.log(f"⚠️ NFC callback error: {e}")
            
            # For non-special tags, also send to agent via websocket
            if phrase not in ("TEST", "AGENT_START"):
                # Regular phrase: force turn end and send to agent via websocket
                import mute_button
                mute_button.trigger_force_turn_end()
                if logNFC: self.log(f"[NFC] Regular tag: {phrase} → forced turn end")
                self.disable()

                # Send or buffer (if no sender yet)
                with self._lock:
                    ws = self._ws
                    loop = self._loop

                if ws and loop:
                    self._send_to_ws(ws, loop, phrase)
                else:
                    self._pending.append(phrase)
                    self.log("ℹ️ No active WS yet; queued NFC phrase.")