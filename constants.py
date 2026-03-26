"""
Shared constants for AIflow (The Orb).
Single source of truth for values used across multiple modules.
"""

# Volume lookup table: API value (1-10) → Raw ALSA value
# Used by config_fetcher.py (boot) and main.py (hot reload)
# Calibrated for the Orb's speaker + amplifier hardware
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
    1: 0      # mute
}
