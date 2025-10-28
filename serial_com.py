import os
import sys
import threading
import atexit
import errno
import termios
import fcntl

__all__ = ["write", "open_port", "close_port", "configure"]

_DEFAULT_PORT = os.getenv("SERIAL_PORT") or os.getenv("SERIAL_DEVICE") or "/dev/ttyUSB0"
_DEFAULT_BAUD = int(os.getenv("SERIAL_BAUD") or 115200)

_fd_lock = threading.Lock()
_fd = None
_port = _DEFAULT_PORT
_baud = _DEFAULT_BAUD

_FALLBACK_PORTS = ("/dev/ttyUSB0", "/dev/ttyACM0", "/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0")

_BAUD_MAP = {
    0: termios.B0, 50: termios.B50, 75: termios.B75, 110: termios.B110,
    134: termios.B134, 150: termios.B150, 200: termios.B200, 300: termios.B300,
    600: termios.B600, 1200: termios.B1200, 1800: termios.B1800, 2400: termios.B2400,
    4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
    57600: termios.B57600, 115200: termios.B115200, 230400: termios.B230400,
    460800: getattr(termios, "B460800", termios.B230400),
    500000: getattr(termios, "B500000", termios.B230400),
    576000: getattr(termios, "B576000", termios.B230400),
    921600: getattr(termios, "B921600", termios.B230400),
}

def _pick_existing_port():
    global _port
    if os.path.exists(_port):
        return _port
    for p in _FALLBACK_PORTS:
        if os.path.exists(p):
            _port = p
            return p
    return _port

def _set_nonblocking(fd: int):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def _configure_fd(fd: int, baud: int):
    if baud not in _BAUD_MAP:
        raise ValueError(f"Unsupported baud rate: {baud}")
    attrs = termios.tcgetattr(fd)
    # raw mode 8N1
    attrs[0] = 0  # iflag
    attrs[1] = 0  # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
    attrs[3] = 0  # lflag
    # speed
    baud_const = _BAUD_MAP[baud]
    if hasattr(termios, "cfsetispeed") and hasattr(termios, "cfsetospeed"):
        termios.cfsetispeed(attrs, baud_const)
        termios.cfsetospeed(attrs, baud_const)
    else:
        # fallback when cfsetispeed/cfsetospeed not available
        attrs[4] = baud_const  # ispeed
        attrs[5] = baud_const  # ospeed
    # control chars
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 5
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

def open_port(port: str = None, baud: int = None):
    global _fd, _port, _baud
    with _fd_lock:
        if _fd is not None:
            return _fd
        if port:
            _port = port
        if baud:
            _baud = int(baud)
        path = _pick_existing_port()
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            sys.stderr.write(f"[serial_com.py] Failed to open {path}: {e}\n")
            return None
        try:
            _configure_fd(fd, _baud)
            _set_nonblocking(fd)
        except Exception as e:
            os.close(fd)
            sys.stderr.write(f"[serial_com.py] Failed to configure {path} at {_baud} baud: {e}\n")
            return None
        _fd = fd
        return _fd

def close_port():
    global _fd
    with _fd_lock:
        if _fd is not None:
            try:
                os.close(_fd)
            except Exception:
                pass
            _fd = None

def configure(port: str = None, baud: int = None):
    global _port, _baud
    if port:
        _port = port
    if baud:
        _baud = int(baud)

def _ensure_open():
    if _fd is None:
        open_port()

def write(char) -> bool:
    global _fd

    # Check if battery initiated shutdown - preserve 'D' animation only
    if os.path.exists('/tmp/battery_shutdown'):
        # Only allow 'D' to be written during battery shutdown
        if isinstance(char, str) and char != 'D':
            return False  # Silently block any animation except 'D'
        elif isinstance(char, (bytes, bytearray, memoryview)):
            if bytes(char) != b'D':
                return False

    if isinstance(char, str):
        data = char.encode("utf-8", errors="ignore")
    elif isinstance(char, (bytes, bytearray, memoryview)):
        data = bytes(char)
    else:
        raise TypeError("serial_com.write() expects str or bytes-like object")
    if not data:
        return True
    _ensure_open()
    with _fd_lock:
        if _fd is None:
            return False
        try:
            n = os.write(_fd, data)
            try:
                termios.tcdrain(_fd)
            except Exception:
                pass
            return n == len(data)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                flags = fcntl.fcntl(_fd, fcntl.F_GETFL)
                fcntl.fcntl(_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                try:
                    n = os.write(_fd, data)
                    try:
                        termios.tcdrain(_fd)
                    except Exception:
                        pass
                    return n == len(data)
                except Exception as e2:
                    sys.stderr.write(f"[serial_com.py] Blocking write failed on {_port}: {e2}\n")
                    return False
            else:
                sys.stderr.write(f"[serial_com.py] Write error on {_port}: {e}; reopening...\n")
                try:
                    close_port()
                    open_port()
                    if _fd is None:
                        return False
                    n = os.write(_fd, data)
                    try:
                        termios.tcdrain(_fd)
                    except Exception:
                        pass
                    return n == len(data)
                except Exception as e3:
                    sys.stderr.write(f"[serial_com.py] Retry after reopen failed: {e3}\n")
                    return False

atexit.register(close_port)

if __name__ == "__main__":
    test_char = "A"
    ok = write(test_char)
    print(f"serial_com.write({test_char!r}) -> {ok} (port={_port}, baud={_baud})")