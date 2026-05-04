from pathlib import Path

from app.core.constants import (
    BASE_DIR,
    APP_DIR,
    BOTS_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    RUNTIME_DIR,
    TMP_DIR,
    TOOLS_DIR,
    VENVS_DIR,
)


def create_desktop_shortcut() -> str:
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
    shortcut = desktop / "OrkaFlow Worker.lnk"

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{shortcut}')
$Shortcut.TargetPath = '{WORKER_BAT_FILE}'
$Shortcut.WorkingDirectory = '{WORKER_BAT_FILE.parent}'
$Shortcut.IconLocation = "$env:SystemRoot\\System32\\cmd.exe"
$Shortcut.Save()
"""

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        shell=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Falha ao criar atalho: {result.stderr or result.stdout}")

    return str(shortcut)


def ensure_tmp_dir() -> Path:
    """
    Garante que a pasta de temporários existe.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return TMP_DIR


def ensure_logs_dir() -> Path:
    """
    Garante que a pasta de logs existe.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def ensure_bots_dir() -> Path:
    """
    Garante que a pasta de bots existe.
    """
    BOTS_DIR.mkdir(parents=True, exist_ok=True)
    return BOTS_DIR


def ensure_venvs_dir() -> Path:
    """
    Garante que a pasta de venvs existe.
    """
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    return VENVS_DIR
