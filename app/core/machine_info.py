# app\core\machine_info.py

"""
Coleta de dados da máquina.
"""

import getpass
import platform
import socket

import psutil


def get_primary_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def get_machine_name() -> str:
    return socket.gethostname()


def get_local_user() -> str:
    return getpass.getuser()


def collect_machine_info() -> dict:
    memory = psutil.virtual_memory()

    return {
        "host_name": socket.gethostname(),
        "ip": get_primary_ip(),
        "os_name": platform.system(),
        "os_version": platform.version(),
        "cpu_arch": platform.machine(),
        "memory_total": int(memory.total / (1024 * 1024)),
        "local_user": get_local_user(),
    }
