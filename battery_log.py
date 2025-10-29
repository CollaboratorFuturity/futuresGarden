#!/usr/bin/env python3
import os
import time
import json
import requests
import subprocess
import threading
from queue import Queue, Empty
from INA219 import INA219
from serial_com import write as serial_write

# ======= CONFIG =======
SUPABASE_URL   = "https://tfsoetwarrsmynpxeazw.supabase.co/functions/v1/update-battery"
DEVICE_API_KEY = os.getenv("LOVABLE_API_KEY")
DEVICE_ID      = os.getenv("DEVICE_ID")

SEND_INTERVAL    = 90      # seconds (upload cadence)
CHECK_INTERVAL   = 30      # seconds (voltage checks)
LOW_VOLTAGE      = 3.65    # V
CRITICAL_VOLTAGE = 3.55    # V
VOLTAGE_THRESHOLD = 0.5   # V 

LOW_COUNT_THRESHOLD = 3        # number of consecutive low-voltage reads to trigger LOW event
CRITICAL_COUNT_THRESHOLD = 3   # number of consecutive critical-voltage reads to trigger shutdown

# Async upload settings
QUEUE_FILE = "/tmp/battery_queue.json"
MAX_RETRY_ATTEMPTS = 3
UPLOAD_TIMEOUT = 5
# ======================

# Global upload queue and worker thread
upload_queue = Queue()
upload_thread = None
shutdown_flag = threading.Event()

def voltage_to_percent(v: float) -> float:
    p = (v - CRITICAL_VOLTAGE) / VOLTAGE_THRESHOLD * 100.0
    return max(0.0, min(100.0, p))

def get_averaged_voltage(ina: INA219, samples: int = 2, delay_ms: int = 50) -> tuple:
    """
    Read voltage and current multiple times and return average.
    Reduces noise and transient spikes for more stable readings.
    
    Args:
        ina: INA219 sensor instance
        samples: Number of samples to average (default: 2)
        delay_ms: Delay between samples in milliseconds (default: 50ms)
    
    Returns:
        tuple: (avg_voltage, avg_current_mA)
    """
    voltages = []
    currents = []
    
    for _ in range(samples):
        try:
            voltages.append(ina.getBusVoltage_V())
            currents.append(ina.getCurrent_mA())
            if len(voltages) < samples:  # Don't delay after last sample
                time.sleep(delay_ms / 1000.0)
        except Exception as e:
            print(f"[INA219] Read error during averaging: {e}")
            # If we have at least one good reading, use it
            if voltages and currents:
                break
            else:
                raise
    
    avg_voltage = sum(voltages) / len(voltages)
    avg_current = sum(currents) / len(currents)
    
    return avg_voltage, avg_current

def load_retry_queue():
    """Load pending uploads from persistent queue file"""
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, 'r') as f:
                items = json.load(f)
                for item in items:
                    upload_queue.put(item)
                print(f"[Supabase] Loaded {len(items)} pending uploads from queue")
                os.remove(QUEUE_FILE)
    except Exception as e:
        print(f"[Supabase] Failed to load retry queue: {e}")

def save_retry_queue():
    """Save pending uploads to persistent queue file"""
    try:
        items = []
        while not upload_queue.empty():
            try:
                items.append(upload_queue.get_nowait())
            except Empty:
                break
        
        if items:
            with open(QUEUE_FILE, 'w') as f:
                json.dump(items, f)
            print(f"[Supabase] Saved {len(items)} pending uploads to queue")
    except Exception as e:
        print(f"[Supabase] Failed to save retry queue: {e}")

def upload_worker():
    """Background thread that processes upload queue"""
    print("[Supabase] Upload worker thread started")
    
    while not shutdown_flag.is_set():
        try:
            # Wait for item with timeout to allow clean shutdown
            item = upload_queue.get(timeout=1.0)
            
            percent = item['percent']
            voltage = item.get('voltage')
            temperature = item.get('temperature')
            attempts = item.get('attempts', 0)

            # Try to upload
            headers = {"Content-Type": "application/json", "x-api-key": DEVICE_API_KEY}
            payload = {
                "device_id": DEVICE_ID,
                "battery": round(percent),
                "voltage": round(voltage, 2) if voltage is not None else None,
                "temperature": temperature
            }

            # Debug: Log what we're about to send
            print(f"[Supabase] DEBUG - Payload before send: {json.dumps(payload)}")
            print(f"[Supabase] DEBUG - Voltage value: {voltage}, Temperature value: {temperature}")

            try:
                r = requests.post(SUPABASE_URL, json=payload, headers=headers, timeout=UPLOAD_TIMEOUT)
                if r.status_code // 100 == 2:
                    print(f"[Supabase] ✓ Uploaded: {percent:.1f}% (queue size: {upload_queue.qsize()})")
                    # Debug: Log what server echoed back
                    print(f"[Supabase] DEBUG - Server response: {r.text[:200]}")
                else:
                    raise Exception(f"HTTP {r.status_code}: {r.text[:120]}")
            except Exception as e:
                attempts += 1
                if attempts < MAX_RETRY_ATTEMPTS:
                    print(f"[Supabase] ⚠️ Upload failed (attempt {attempts}/{MAX_RETRY_ATTEMPTS}): {e}")
                    # Re-queue with incremented attempt counter
                    item['attempts'] = attempts
                    upload_queue.put(item)
                else:
                    print(f"[Supabase] ✗ Upload failed after {MAX_RETRY_ATTEMPTS} attempts, dropping: {e}")
            
            upload_queue.task_done()
            
        except Empty:
            # Timeout, check shutdown flag and continue
            continue
        except Exception as e:
            print(f"[Supabase] Worker error: {e}")
            time.sleep(1)
    
    print("[Supabase] Upload worker thread stopped")

def start_upload_worker():
    """Initialize and start the background upload thread"""
    global upload_thread
    
    # Load any pending uploads from previous run
    load_retry_queue()
    
    # Start worker thread
    upload_thread = threading.Thread(target=upload_worker, daemon=True, name="SupabaseUploader")
    upload_thread.start()

def queue_upload(percent: float, voltage: float = None, temperature: str = None):
    """Queue a battery reading for async upload (non-blocking)"""
    upload_queue.put({
        'percent': percent,
        'voltage': voltage,
        'temperature': temperature,
        'attempts': 0,
        'timestamp': time.time()
    })

def stop_upload_worker():
    """Gracefully stop the upload worker and save pending uploads"""
    print("[Supabase] Stopping upload worker...")
    shutdown_flag.set()
    
    if upload_thread and upload_thread.is_alive():
        upload_thread.join(timeout=3.0)
    
    # Save any remaining items in queue
    save_retry_queue()

def show_battery_icon():
    try:
        serial_write('V')
    except Exception as e:
        print(f"[Serial] Write error: {e}")

def get_system_health():
    """Get comprehensive system health metrics"""
    try:
        # Check temperature
        temp_raw = subprocess.check_output(['vcgencmd', 'measure_temp'], timeout=1).decode()
        temp = temp_raw.strip().replace("temp=", "").replace("'C", "°C")
        
        # Check memory
        mem_raw = subprocess.check_output(['free', '-m'], timeout=1).decode().split('\n')[1].split()
        mem_used = int(mem_raw[2])
        mem_total = int(mem_raw[1])
        mem_pct = (mem_used / mem_total) * 100
        
        # Check throttling/under-voltage
        throttle_raw = subprocess.check_output(['vcgencmd', 'get_throttled'], timeout=1).decode()
        throttle_hex = int(throttle_raw.split('=')[1], 16)
        
        status_flags = []
        if throttle_hex & 0x1:
            status_flags.append("⚠️UV_NOW")
        if throttle_hex & 0x10000:
            status_flags.append("UV_PAST")
        if throttle_hex & 0x2:
            status_flags.append("THROTTLE_NOW")
        
        status_str = " ".join(status_flags) if status_flags else "OK"
        
        return {
            'temp': temp,
            'mem_pct': mem_pct,
            'status': status_str,
            'under_voltage': bool(throttle_hex & 0x1)
        }
    except Exception as e:
        return {
            'temp': 'error',
            'mem_pct': 0,
            'status': f'check_failed: {e}',
            'under_voltage': False
        }

def safe_shutdown():
    os.system("sudo poweroff")

def main():
    ina = INA219(addr=0x43)
    last_upload = 0.0
    low_count = 0
    critical_count = 0
    
    # Start async upload worker thread
    start_upload_worker()

    try:
        while True:
            try:
                # Use averaged dual-read for stable measurements (±0.02V accuracy)
                v, current_mA = get_averaged_voltage(ina, samples=2, delay_ms=50)
                pct = voltage_to_percent(v)
                now = time.monotonic()
                
                # Get system health metrics
                health = get_system_health()

                # Upload every SEND_INTERVAL (async, non-blocking)
                if now - last_upload >= SEND_INTERVAL:
                    print(f"[Battery] {pct:.1f}% ({v:.3f}V @ {current_mA:.1f}mA) | "
                          f"Temp:{health['temp']} Mem:{health['mem_pct']:.0f}% {health['status']}")
                    queue_upload(pct, voltage=v, temperature=health['temp'])  # Non-blocking async upload
                    last_upload = now
                
                # Check for Pi under-voltage (more critical than battery voltage!)
                if health['under_voltage']:
                    print(f"[Battery] 🚨 Pi reports UNDER-VOLTAGE! Battery:{v:.3f}V Current:{current_mA:.1f}mA")
                    print("[Battery] ⚠️ Immediate shutdown to prevent corruption!")
                    # Create flag to lock serial port to 'D' animation only
                    try:
                        open('/tmp/battery_shutdown', 'w').close()
                    except Exception:
                        pass
                    serial_write('D')
                    time.sleep(2)
                    safe_shutdown()
                    return

                # --- CRITICAL voltage section ---
                if v <= CRITICAL_VOLTAGE:
                    critical_count += 1
                    print(f"[Battery] 🔴  CRITICAL voltage {critical_count}/{CRITICAL_COUNT_THRESHOLD} "
                          f"({v:.3f}V @ {current_mA:.1f}mA)")
                    if critical_count >= CRITICAL_COUNT_THRESHOLD:
                        print(f"[Battery] ⚠️  Sustained CRITICAL voltage — initiating shutdown.")
                        print(f"[Battery] Final: {v:.3f}V @ {current_mA:.1f}mA | {health['status']}")
                        # Create flag to lock serial port to 'D' animation only
                        try:
                            open('/tmp/battery_shutdown', 'w').close()
                        except Exception:
                            pass
                        serial_write('D')
                        time.sleep(2)
                        safe_shutdown()
                        return
                else:
                    if critical_count > 0:
                        print(f"[Battery] ✅  Voltage recovered above CRITICAL ({v:.3f} V) — resetting critical counter.")
                    critical_count = 0

                # --- LOW voltage section ---
                if CRITICAL_VOLTAGE < v <= LOW_VOLTAGE:
                    low_count += 1
                    print(f"[Battery] 🟠  LOW voltage {low_count}/{LOW_COUNT_THRESHOLD} "
                          f"({v:.3f}V @ {current_mA:.1f}mA)")
                    if low_count >= LOW_COUNT_THRESHOLD:
                        print(f"[Battery] ⚠️  Sustained LOW voltage — warning state triggered.")
                        print(f"[Battery] Status: Temp:{health['temp']} Mem:{health['mem_pct']:.0f}% {health['status']}")
                        show_battery_icon()
                else:
                    if low_count > 0 and v > LOW_VOLTAGE:
                        print(f"[Battery] ✅  Voltage normal ({v:.3f} V) — resetting low counter.")
                    low_count = 0

            except Exception as e:
                print(f"[Loop] Error: {e}")

            time.sleep(CHECK_INTERVAL)
    
    except KeyboardInterrupt:
        print("[Battery] Keyboard interrupt received")
    finally:
        stop_upload_worker()

if __name__ == "__main__":
    main()