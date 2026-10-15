"""One private, reusable SSH transport per qualification driver."""
import atexit
from pathlib import Path
import subprocess
import tempfile

_directory = tempfile.TemporaryDirectory(prefix='wan-watchdog-ssh-')
_socket = str(Path(_directory.name) / 'router')
MUX = ['-o', 'ControlMaster=auto', '-o', 'ControlPersist=60',
       '-o', 'ControlPath=' + _socket]


@atexit.register
def close_transport():
    try:
        if Path(_socket).exists():
            subprocess.run(['ssh', '-o', 'ControlPath=' + _socket, '-O', 'exit', 'router'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        _directory.cleanup()
