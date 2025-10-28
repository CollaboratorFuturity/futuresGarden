# mute_button.py
# Threaded mute toggle on GPIO12 (to GND) using Blinka (board, digitalio).
# Exposes: start_mute_button(), is_muted(), stop_mute_button()

import time
import threading
import serial_com
logBut = False
try:
    import board
    import digitalio
    _HAS_BLINKA = True
except Exception:
    _HAS_BLINKA = False

# Shared state
__MUTED = threading.Event()
__MUTED.set()  # Start muted by default (button must be pressed to start speaking)
__STOP  = threading.Event()
__THREAD = None
__BTN = None
__STATE_CHECK = None  # Callback to check if button should be active

def is_muted() -> bool:
    """Return True if the mic should be muted."""
    return __MUTED.is_set()

def _toggle():
    if __MUTED.is_set():
        __MUTED.clear()
        print("[Mute] UNMUTED")
    else:
        __MUTED.set()
        print("[Mute] MUTED")

def _watch_loop(pin_obj, debounce_s: float, poll_s: float):
    """
    Enhanced watcher with press duration gating and proper debounce:
      - Press starts recording immediately (unmute right away)
      - If released before PRESS_MIN_MS → short press ignored, re-mute silently
      - If held >= PRESS_MIN_MS → normal behavior
    This prevents quick accidental taps from ending a conversation turn or triggering retry prompts.
    Debounce filtering eliminates mechanical bounce noise and current spikes.
    """
    print("[Mute] Button watcher running.")
    PRESS_MIN_MS = 1000  # 1s minimum valid press
    DEBOUNCE_MS = 50     # 50ms debounce period to filter mechanical bounce
    last_pressed = False
    press_start_time = None

    while not __STOP.is_set():
        try:
            # Check if button should be active (only in running_agent state)
            if __STATE_CHECK and not __STATE_CHECK():
                # Button disabled in current state - just poll and skip processing
                time.sleep(poll_s)
                continue

            # Read raw pin state
            raw_pressed = not pin_obj.value  # active-low: pressed = False logic

            # Debounce: if state changed, wait and verify it's stable
            if raw_pressed != last_pressed:
                time.sleep(DEBOUNCE_MS / 1000.0)
                stable_pressed = not pin_obj.value
                # Only accept the change if it's still the same after debounce
                if stable_pressed != raw_pressed:
                    # Bouncing detected - ignore this transition
                    time.sleep(poll_s)
                    continue
                pressed = stable_pressed
            else:
                pressed = raw_pressed

            now = time.time()

            # Button just pressed (after debounce)
            if pressed and not last_pressed:
                press_start_time = now
                # Always unmute immediately to allow instant speech
                if __MUTED.is_set():
                    __MUTED.clear()
                    if logBut: print("[Mute] UNMUTED (button pressed)")
                    serial_com.write('U')  # Unmuted - ready to record

            # Button just released
            if not pressed and last_pressed:
                if press_start_time:
                    duration_ms = (now - press_start_time) * 1000.0
                    if duration_ms < PRESS_MIN_MS:
                        # Short press — silently revert without causing a turn end
                        if not __MUTED.is_set():
                            __MUTED.set()
                            if logBut: print(f"[Mute] Short press ignored ({duration_ms:.0f}ms → revert to MUTED)")
                            serial_com.write('M')  # Muted - button released
                        # DO NOT trigger force_turn_end or inject silence
                    else:
                        # Long press — normal mute transition on release
                        if not __MUTED.is_set():
                            __MUTED.set()
                            if logBut: print(f"[Mute] MUTED (held {duration_ms:.0f}ms)")
                            serial_com.write('M')  # Muted - button released
                press_start_time = None

            last_pressed = pressed
            time.sleep(poll_s)
        except Exception as e:
            if logBut: print(f"[Mute] Warning: {e}")
            time.sleep(0.1)
    if logBut: print("[Mute] Button watcher stopped.")

def set_state_check(callback):
    """
    Set a callback function that returns True when button should be active.
    Example: set_state_check(lambda: get_state() == "running_agent")
    """
    global __STATE_CHECK
    __STATE_CHECK = callback

def start_mute_button(pin= "D12", debounce_s: float = 0.5, poll_s: float = 0.01):
    """
    Start the GPIO12 (default) watcher in a daemon thread.
    Wiring: GPIO12 -> button -> GND. Internal pull-up enabled.
    Returns True if started, False if Blinka not available.
    """
    global __THREAD, __BTN
    if __THREAD is not None:
        return True  # already started

    if not _HAS_BLINKA:
        print("[Mute] Blinka not available; mute button disabled.")
        return False

    # Resolve pin object (default "D12")
    pin_obj = getattr(board, pin) if isinstance(pin, str) else pin

    # Configure input with pull-up
    btn = digitalio.DigitalInOut(pin_obj)
    btn.direction = digitalio.Direction.INPUT
    btn.pull = digitalio.Pull.UP
    __BTN = btn

    __STOP.clear()

    t = threading.Thread(target=_watch_loop, args=(btn, debounce_s, poll_s), daemon=True)
    t.start()
    __THREAD = t

    if logBut: print(f"[Mute] Initialized on {pin} (pull-up), debounce={debounce_s*1000:.0f} ms.")
    if logBut: print("[Mute] Press button to toggle mic mute/unmute.")
    return True
def force_mute():
    """Programmatically set the mic to muted state (as if button released)."""
    global __MUTED
    if not __MUTED.is_set():
        __MUTED.set()
        print("[Mute] MUTED (forced by code)")

def stop_mute_button():
    """Stop the watcher and deinit the pin. Safe to call multiple times."""
    global __THREAD, __BTN
    __STOP.set()
# Global event to force turn end (for NFC, etc.)
force_turn_end = threading.Event()

def trigger_force_turn_end():
    """Set the force_turn_end event to signal the main loop to end the turn."""
    force_turn_end.set()
