#!/usr/bin/env python3
"""
Configuration Fetcher for AIflow
Fetches configuration from remote web server, maps agent names to IDs,
saves to .env file, sets system volume, and launches main.py
"""

import os
import sys
import time
import json
import logging
import subprocess
import requests
from pathlib import Path
from typing import Dict, Optional

# Configuration
# Get DEVICE_ID from system environment (must be set before running this script)
DEVICE_ID = os.getenv("DEVICE_ID")
if not DEVICE_ID:
    print("ERROR: DEVICE_ID environment variable not set!")
    print("Set it with: export DEVICE_ID='your_device_id'")
    sys.exit(1)

# Construct API URL with device_id parameter
API_URL = f"https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/get-device-config?device_id={DEVICE_ID}"
ENV_FILE_PATH = "/tmp/aiflow.env"  # tmpfs - RAM-based storage
LOG_FILE_PATH = "/tmp/config_fetcher.log"  # tmpfs - RAM-based storage
MAIN_PY_PATH = "/home/orb/AIflow/main.py"  # Read-only filesystem
WIFI_CONFIG_PATH = "/boot/wifi_config.txt"  # Persistent WiFi credentials
MAX_RETRIES = 5
RETRY_DELAY = 5  # seconds
NETWORK_WAIT_TIMEOUT = 60  # seconds

# Agent name to ID mapping (UPDATE THESE WITH ACTUAL AGENT IDs)
AGENT_NAME_TO_ID = {
    "Zane": "uHlKfBtzRYokBFLcCOjq",
    "Rowan": "agent_01jvs5f45jepab76tr81m51gdx",
    "Nova": "agent_1701k5bgdzmte5f9q518mge3jsf0",
    "Cypher": "agent_01jvwd88bdeeftgh3kxrx1k4sk"
}

# Setup logging
# Note: Only use FileHandler since systemd redirects stdout to the same file
# Using both would cause duplicate log entries
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH)
    ]
)
logger = logging.getLogger(__name__)


def wait_for_network(timeout: int = NETWORK_WAIT_TIMEOUT) -> bool:
    """
    Wait for network connectivity before proceeding.
    
    Args:
        timeout: Maximum time to wait in seconds
        
    Returns:
        True if network is available, False if timeout reached
    """
    logger.info("Checking network connectivity...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # Try to reach a reliable DNS server
            response = requests.get("https://1.1.1.1", timeout=3)
            if response.status_code == 200:
                logger.info("Network connectivity confirmed")
                return True
        except requests.RequestException:
            logger.debug("Network not ready, waiting...")
            time.sleep(2)
    
    logger.error(f"Network connectivity timeout after {timeout} seconds")
    return False


def fetch_config_from_api(url: str, retries: int = MAX_RETRIES) -> Optional[Dict]:
    """
    Fetch configuration from remote API with retry logic.
    
    Args:
        url: API endpoint URL
        retries: Maximum number of retry attempts
        
    Returns:
        Dictionary containing configuration or None on failure
    """
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"Fetching configuration from API (attempt {attempt}/{retries})...")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            config = response.json()
            logger.info("Configuration fetched successfully")
            return config
            
        except requests.RequestException as e:
            logger.error(f"API request failed: {e}")
            if attempt < retries:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached, unable to fetch configuration")
                return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            return None
    
    return None


def map_agent_name_to_id(agent_name: str) -> Optional[str]:
    """
    Map agent name from API to actual agent ID.
    
    Args:
        agent_name: Agent name from API (e.g., "Zane", "Rowan", "Nova", "Cypher")
        
    Returns:
        Mapped agent ID string or None if not found
    """
    agent_id = AGENT_NAME_TO_ID.get(agent_name)
    
    if agent_id:
        logger.info(f"Mapped agent name '{agent_name}' to ID: {agent_id}")
    else:
        logger.error(f"Unknown agent name: {agent_name}")
        logger.error(f"Available agents: {list(AGENT_NAME_TO_ID.keys())}")
    
    return agent_id


def write_env_file(config: Dict, env_path: str) -> bool:
    """
    Write configuration to .env file with agent name mapping.
    Extracts only necessary fields: agent_id, volume, id, name, wifi.ssid, wifi.password
    
    Args:
        config: Configuration dictionary from API
        env_path: Path to .env file
        
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Writing configuration to {env_path}...")
        
        # Extract and validate required fields
        agent_name = config.get("agent_id")
        if not agent_name:
            logger.error("Missing 'agent_id' in API response")
            return False
        
        # Map agent name to actual agent ID
        agent_id = map_agent_name_to_id(agent_name)
        if not agent_id:
            logger.error("Failed to map agent name to ID")
            return False
        
        # Extract volume (optional, defaults to 75)
        volume = config.get("volume", 75)

        # Extract input mode (optional, defaults to PTT)
        input_mode = config.get("input_mode", "PTT").upper()
        if input_mode not in ["PTT", "VAD"]:
            logger.warning(f"Invalid input_mode '{input_mode}', defaulting to PTT")
            input_mode = "PTT"

        # Extract device info (optional)
        device_id = config.get("id", "unknown")
        device_name = config.get("name", "unknown")
        
        # Extract wifi credentials (optional)
        wifi = config.get("wifi", {})
        wifi_ssid = wifi.get("ssid", "")
        wifi_password = wifi.get("password", "")
        
        # Create directory if it doesn't exist
        Path(env_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Write to .env file (only the fields we need)
        with open(env_path, 'w') as f:
            f.write("# AIflow Configuration\n")
            f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Device: {device_name} ({device_id})\n\n")
            
            # Agent configuration
            f.write(f"AGENT_ID={agent_id}\n")

            # System configuration
            f.write(f"VOLUME={volume}\n")
            f.write(f"INPUT_MODE={input_mode}\n")

            # Device information
            f.write(f"DEVICE_ID={device_id}\n")
            f.write(f"DEVICE_NAME={device_name}\n")
            
            # WiFi credentials (if available)
            if wifi_ssid:
                f.write(f"WIFI_SSID={wifi_ssid}\n")
            if wifi_password:
                f.write(f"WIFI_PASSWORD={wifi_password}\n")
        
        logger.info(f"Successfully wrote configuration to .env file")
        logger.info(f"  Agent: {agent_name} → {agent_id}")
        logger.info(f"  Volume: {volume}")
        logger.info(f"  Input Mode: {input_mode}")
        logger.info(f"  Device: {device_name} ({device_id})")
        if wifi_ssid:
            logger.info(f"  WiFi: {wifi_ssid}")
        
        return True
        
    except IOError as e:
        logger.error(f"Failed to write .env file: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error writing .env file: {e}")
        return False


def apply_system_volume(config: Dict) -> bool:
    """
    Set system volume using ALSA amixer command.
    Uses a 1-10 scale mapped to calibrated raw values for proper dB scaling.
    
    Volume Mapping:
    10 → 127 (100%), 9 → 124 (89%), 8 → 121 (79%), 7 → 118 (71%)
    6 → 114 (61%), 5 → 110 (52%), 4 → 104 (41%), 3 → 96 (30%)
    2 → 85 (20%), 1 → 65 (9%)
    
    Args:
        config: Configuration dictionary containing 'volume' key (1-10)
        
    Returns:
        True if successful, False otherwise
    """
    volume = config.get("volume")
    
    if volume is None:
        logger.warning("No volume setting in configuration, skipping volume adjustment")
        return True
    
    # Volume lookup table: API value (1-10) → Raw ALSA value → Actual %
    VOLUME_MAP = {
        10: 124,  # 100%
        9: 121,   # 89%
        8: 118,   # 79%
        7: 114,   # 71%
        6: 110,   # 61%
        5: 104,   # 52%
        4: 96,   # 41%
        3: 85,    # 30%
        2: 65,    # 20%
        1: 0     # 9%
    }
    
    try:
        volume_int = int(volume)
        
        if volume_int not in VOLUME_MAP:
            logger.error(f"Invalid volume value: {volume_int} (must be 1-10)")
            return False
        
        raw_value = VOLUME_MAP[volume_int]
        
        logger.info(f"Setting system volume to level {volume_int}/10 (raw value: {raw_value})...")
        
        # Use amixer to set Speaker volume with calibrated raw value
        result = subprocess.run(
            ["amixer", "set", "Speaker", str(raw_value)],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logger.info(f"System volume set to level {volume_int}/10 successfully")
            return True
        else:
            logger.error(f"Failed to set volume: {result.stderr}")
            return False
            
    except ValueError as e:
        logger.error(f"Invalid volume format: {e}")
        return False
    except FileNotFoundError:
        logger.error("amixer command not found - is ALSA installed?")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Volume adjustment timed out")
        return False
    except Exception as e:
        logger.error(f"Unexpected error setting volume: {e}")
        return False


def load_saved_wifi() -> Optional[tuple]:
    """
    Load WiFi credentials from persistent storage.
    
    Returns:
        Tuple of (ssid, password) if found, None otherwise
    """
    if not os.path.exists(WIFI_CONFIG_PATH):
        logger.info("No saved WiFi configuration found")
        return None
    
    try:
        logger.info(f"Loading saved WiFi credentials from {WIFI_CONFIG_PATH}")
        with open(WIFI_CONFIG_PATH, 'r') as f:
            lines = f.readlines()
        
        ssid = None
        password = None
        
        for line in lines:
            line = line.strip()
            if line.startswith('SSID='):
                ssid = line.split('=', 1)[1]
            elif line.startswith('PASSWORD='):
                password = line.split('=', 1)[1]
        
        if ssid and password:
            logger.info(f"Found saved WiFi: {ssid}")
            return (ssid, password)
        else:
            logger.warning("WiFi config file incomplete")
            return None
            
    except Exception as e:
        logger.error(f"Error loading saved WiFi: {e}")
        return None


def configure_wifi(ssid: str, password: str) -> bool:
    """
    Configure WiFi network using NetworkManager (nmcli).
    Saves credentials to /boot/wifi_config.txt for persistence,
    then connects using nmcli (works in read-only mode).
    
    Args:
        ssid: WiFi network SSID
        password: WiFi network password
        
    Returns:
        True if successful, False otherwise
    """
    if not ssid or not password:
        logger.warning("No WiFi credentials provided, skipping WiFi configuration")
        return True
    
    try:
        logger.info(f"Configuring WiFi network: {ssid}")
        
        # Check if nmcli is available
        try:
            subprocess.run(["nmcli", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.error("nmcli not found - is NetworkManager installed?")
            return False
        
        # Check if connection already exists (do this FIRST to avoid unnecessary operations)
        check_result = subprocess.run(
            ["nmcli", "connection", "show", ssid],
            capture_output=True,
            text=True
        )
        
        if check_result.returncode == 0:
            logger.info(f"WiFi network '{ssid}' already saved - skipping configuration")
            return True
        
        # Connection doesn't exist - need to configure it
        # Ensure NetworkManager connections mount is writable
        # (System boots in RO mode, need to remount as RW and restart NetworkManager)
        logger.info("Ensuring NetworkManager connections directory is writable...")
        try:
            # Remount as read-write
            subprocess.run(
                ["sudo", "mount", "-o", "remount,rw", "/etc/NetworkManager/system-connections"],
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.info("NetworkManager connections directory remounted as writable")
            
            # Reload NetworkManager config (lighter than restart, doesn't kill connections)
            logger.info("Reloading NetworkManager configuration...")
            subprocess.run(
                ["sudo", "nmcli", "general", "reload"],
                capture_output=True,
                timeout=5,
                check=True
            )
            logger.info("NetworkManager configuration reloaded")
            
            # Brief pause
            time.sleep(1)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to prepare NetworkManager: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error preparing NetworkManager: {e}")
            return False
        
        # Connect to WiFi using nmcli device wifi connect
        # Since /etc/NetworkManager/system-connections is writable (persistent mount),
        # this will save the connection and auto-connect on future boots
        logger.info(f"Connecting to WiFi and saving to persistent storage: {ssid}")
        
        result = subprocess.run(
            ["sudo", "nmcli", "device", "wifi", "connect", ssid, "password", password],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            logger.info(f"Successfully connected to WiFi: {ssid}")
            logger.info("Connection saved to persistent storage - will auto-connect on next boot")
            return True
        else:
            logger.error(f"Failed to connect to WiFi: {result.stderr}")
            # Even if connection fails (network not in range), try to add it manually
            logger.info("Attempting to add connection profile for future use...")
            
            # Fallback: try to create connection without connecting
            fallback_result = subprocess.run(
                ["sudo", "nmcli", "connection", "add", "type", "wifi", "con-name", ssid,
                 "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if fallback_result.returncode == 0:
                logger.info(f"Added WiFi profile for future use: {ssid}")
                return True
            else:
                logger.error(f"Fallback also failed: {fallback_result.stderr}")
                return False
            
    except subprocess.TimeoutExpired:
        logger.error("WiFi configuration timed out")
        return False
    except Exception as e:
        logger.error(f"Unexpected error configuring WiFi: {e}")
        return False


def check_and_apply_updates():
    """
    Check for updates from GitHub and apply if newer version available.
    This function checks GitHub Releases API for the latest version,
    compares to installed version, and downloads/installs if needed.

    Returns:
        bool: True if update was applied (will reboot), False if no update needed
    """
    GITHUB_REPO = "CollaboratorFuturity/futuresGarden"
    GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    VERSION_FILE = "/home/orb/AIflow/version"  # Version file in the repo
    CODE_DIR = "/home/orb/AIflow"
    BACKUP_DIR = "/home/orb/AIflow.backup"

    # Data folders/files to preserve during update
    # These are agent-specific data folders (named by agent ID, not agent name)
    PRESERVE_ITEMS = [
        "uHlKfBtzRYokBFLcCOjq",                    # Zane
        "agent_01jvs5f45jepab76tr81m51gdx",        # Rowan
        "agent_1701k5bgdzmte5f9q518mge3jsf0",      # Nova
        "agent_01jvwd88bdeeftgh3kxrx1k4sk",        # Cypher
        "beep.wav",                                 # Shared beep sound
    ]

    logger.info("=" * 50)
    logger.info("Checking for software updates...")
    logger.info("=" * 50)

    try:
        # Read installed version
        try:
            with open(VERSION_FILE, 'r') as f:
                installed_version = f.read().strip()
            logger.info(f"Installed version: {installed_version}")
        except FileNotFoundError:
            installed_version = "v0.0.0"
            logger.warning(f"No version file found, assuming {installed_version}")

        # Check GitHub for latest release (with timeout)
        try:
            logger.info(f"Checking GitHub: {GITHUB_API_URL}")
            response = requests.get(GITHUB_API_URL, timeout=10)
            response.raise_for_status()
            release_data = response.json()
            latest_version = release_data['tag_name']
            download_url = release_data['tarball_url']
            logger.info(f"Latest version available: {latest_version}")
        except requests.RequestException as e:
            logger.warning(f"Failed to check for updates: {e}")
            logger.info("Continuing with current version...")
            return False
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse GitHub API response: {e}")
            return False

        # Compare versions
        if latest_version == installed_version:
            logger.info("✓ Already on latest version")
            return False

        logger.info(f"🔄 Update available: {installed_version} → {latest_version}")
        logger.info("Downloading update...")

        # Download update tarball
        import tempfile
        import tarfile
        import shutil

        temp_dir = tempfile.mkdtemp(prefix="aiflow_update_")
        tarball_path = os.path.join(temp_dir, "update.tar.gz")

        try:
            # Download with timeout
            logger.info(f"Downloading from: {download_url}")
            dl_response = requests.get(download_url, timeout=60, stream=True)
            dl_response.raise_for_status()

            with open(tarball_path, 'wb') as f:
                for chunk in dl_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"✓ Downloaded {os.path.getsize(tarball_path)} bytes")

            # Extract tarball
            logger.info("Extracting update...")
            with tarfile.open(tarball_path, 'r:gz') as tar:
                tar.extractall(temp_dir)

            # Find extracted directory (GitHub adds repo name + hash)
            extracted_dirs = [d for d in os.listdir(temp_dir)
                            if os.path.isdir(os.path.join(temp_dir, d))]
            if not extracted_dirs:
                raise Exception("No directory found in tarball")

            extracted_dir = os.path.join(temp_dir, extracted_dirs[0])
            logger.info(f"✓ Extracted to: {extracted_dir}")

            # Debug: List contents of extracted directory
            try:
                contents = os.listdir(extracted_dir)
                logger.info(f"Contents of extracted directory: {contents}")
            except Exception as e:
                logger.warning(f"Could not list extracted directory: {e}")

            # Validate update (check for critical files)
            required_files = ["main.py", "config_fetcher.py", "nfc_backend.py"]
            for req_file in required_files:
                file_path = os.path.join(extracted_dir, req_file)
                if not os.path.exists(file_path):
                    logger.error(f"Expected file at: {file_path}")
                    raise Exception(f"Missing required file: {req_file}")

            logger.info("✓ Update validation passed")

            # Enable RW mode
            logger.info("Enabling read-write filesystem...")
            result = subprocess.run(["sudo", "rwro", "rw"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise Exception(f"Failed to enable RW mode: {result.stderr}")

            logger.info("✓ Filesystem is read-write")

            try:
                # Backup current installation
                logger.info("Backing up current installation...")
                if os.path.exists(BACKUP_DIR):
                    shutil.rmtree(BACKUP_DIR)
                shutil.copytree(CODE_DIR, BACKUP_DIR)
                logger.info(f"✓ Backup created at {BACKUP_DIR}")

                # Backup version file
                if os.path.exists(VERSION_FILE):
                    shutil.copy(VERSION_FILE, VERSION_FILE + ".backup")

                # Preserve data folders
                logger.info("Preserving data folders...")
                preserve_temp = os.path.join(temp_dir, "preserved_data")
                os.makedirs(preserve_temp, exist_ok=True)

                for item in PRESERVE_ITEMS:
                    item_path = os.path.join(CODE_DIR, item)
                    if os.path.exists(item_path):
                        dest = os.path.join(preserve_temp, item)
                        if os.path.isdir(item_path):
                            shutil.copytree(item_path, dest)
                        else:
                            shutil.copy(item_path, dest)
                        logger.info(f"  ✓ Preserved: {item}")

                # Replace code directory
                logger.info("Installing new version...")
                shutil.rmtree(CODE_DIR)

                # Copy files from extracted_dir -> CODE_DIR
                shutil.copytree(extracted_dir, CODE_DIR)
                logger.info(f"✓ Installed new code to {CODE_DIR}")

                # Restore preserved data
                logger.info("Restoring preserved data...")
                for item in os.listdir(preserve_temp):
                    src = os.path.join(preserve_temp, item)
                    dest = os.path.join(CODE_DIR, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dest)
                    else:
                        shutil.copy(src, dest)
                    logger.info(f"  ✓ Restored: {item}")

                # Update version file
                with open(VERSION_FILE, 'w') as f:
                    f.write(latest_version)
                logger.info(f"✓ Updated version file to {latest_version}")

                # Return to RO mode
                logger.info("Returning to read-only filesystem...")
                subprocess.run(["sudo", "rwro", "ro"], capture_output=True, timeout=5)

                logger.info("=" * 50)
                logger.info(f"✓ Update complete: {installed_version} → {latest_version}")
                logger.info("Rebooting to apply changes...")
                logger.info("=" * 50)

                # Cleanup temp directory
                shutil.rmtree(temp_dir, ignore_errors=True)

                # Reboot to apply update
                time.sleep(2)
                subprocess.run(["sudo", "reboot"], timeout=5)
                sys.exit(0)  # Never reached, but explicit

            except Exception as e:
                logger.error(f"Update installation failed: {e}")
                logger.info("Attempting to restore from backup...")

                try:
                    # Restore backup
                    if os.path.exists(BACKUP_DIR):
                        if os.path.exists(CODE_DIR):
                            shutil.rmtree(CODE_DIR)
                        shutil.move(BACKUP_DIR, CODE_DIR)
                        logger.info("✓ Restored from backup")

                    # Restore version file
                    backup_version = VERSION_FILE + ".backup"
                    if os.path.exists(backup_version):
                        shutil.copy(backup_version, VERSION_FILE)

                    # Return to RO mode
                    subprocess.run(["sudo", "rwro", "ro"], capture_output=True, timeout=5)

                except Exception as restore_error:
                    logger.error(f"Failed to restore backup: {restore_error}")
                    logger.error("CRITICAL: System may be in inconsistent state")

                raise

        finally:
            # Cleanup temp directory
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass

    except Exception as e:
        logger.error(f"Update check/apply failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.info("Continuing with current version...")
        return False

    return False


def transition_to_main_app(main_py_path: str):
    """
    Transition execution to main.py by replacing current process.
    Config fetcher prepares environment, then hands off to main.py.
    This keeps all logs visible and avoids subprocess issues.
    
    Args:
        main_py_path: Path to main.py file
        
    Note:
        This function does not return - it replaces the current process with main.py
    """
    try:
        if not os.path.exists(main_py_path):
            logger.error(f"main.py not found at: {main_py_path}")
            sys.exit(1)
        
        logger.info("=" * 50)
        logger.info("Configuration complete - transitioning to main.py")
        logger.info("=" * 50)
        logger.info("")
        
        # Change to main.py directory
        os.chdir(os.path.dirname(main_py_path))
        
        # Use os.execv to replace current process with main.py
        # This keeps the same PID and all file descriptors (including stdout/stderr)
        os.execv(sys.executable, [sys.executable, main_py_path])
        
        # This line never executes - process has been replaced
        
    except Exception as e:
        logger.error(f"Failed to transition to main application: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


def main():
    """
    Main execution flow:
    1. Wait for network connectivity (on default/setup hotspot)
    2. Try to fetch configuration from API
    3. If API fetch fails, load saved WiFi and try to connect
    4. Map agent name to agent ID
    5. Write to .env file
    6. Configure WiFi network
    7. Apply system volume
    8. Launch main.py
    """
    logger.info("=" * 50)
    logger.info("AIflow Configuration Fetcher Started")
    logger.info("=" * 50)
    
    # Step 1: Wait for network (expect setup hotspot or existing connection)
    if not wait_for_network():
        logger.warning("No network connectivity on startup")
        
        # Try to load and connect to saved WiFi
        saved_wifi = load_saved_wifi()
        if saved_wifi:
            ssid, password = saved_wifi
            logger.info(f"Attempting to connect to saved WiFi: {ssid}")
            if configure_wifi(ssid, password):
                logger.info("Connected to saved WiFi, waiting for network...")
                if not wait_for_network(timeout=30):
                    logger.error("Still no network after WiFi connection")
                    sys.exit(1)
            else:
                logger.error("Failed to connect to saved WiFi")
                sys.exit(1)
        else:
            logger.error("No saved WiFi found and no network available")
            logger.error("Expected to connect to setup hotspot first!")
            sys.exit(1)
    
    # Step 2: Fetch configuration from API
    config = fetch_config_from_api(API_URL)
    if not config:
        logger.error("Failed to fetch configuration from API")
        logger.error("Cannot proceed without configuration")
        sys.exit(1)
    
    logger.info(f"Fetched configuration keys: {list(config.keys())}")
    
    # Step 3 & 4: Map agent name and write .env file
    if not write_env_file(config, ENV_FILE_PATH):
        logger.error("Failed to write .env file")
        sys.exit(1)
    
    # Step 5: Configure WiFi (save new credentials and connect)
    wifi = config.get("wifi", {})
    if wifi:
        wifi_ssid = wifi.get("ssid", "")
        wifi_password = wifi.get("password", "")
        if wifi_ssid and wifi_password:
            if not configure_wifi(wifi_ssid, wifi_password):
                logger.warning("WiFi configuration failed, but continuing...")
        else:
            logger.warning("No WiFi credentials in API response")
    
    # Step 6: Apply system volume
    if not apply_system_volume(config):
        logger.warning("Volume adjustment failed, but continuing...")

    # Step 7: Check for software updates (will reboot if update applied)
    check_and_apply_updates()

    # Step 8: Transition to main application (this does not return)
    transition_to_main_app(MAIN_PY_PATH)
    
    # This line never executes - process has been replaced by main.py


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Configuration fetcher interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}", exc_info=True)
        sys.exit(1)