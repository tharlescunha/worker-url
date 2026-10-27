"""Print da tela sem depender de biblioteca: usa mss/Pillow se o robô tiver, senão PowerShell."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def capture_screen(destination: Path) -> bool:
    """Grava um PNG em ``destination``. Devolve False (sem exceção) quando não conseguiu."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _capture_with_mss(destination):
        return True
    if sys.platform.startswith("win") and _capture_with_powershell(destination):
        return True
    return False


def _capture_with_mss(destination: Path) -> bool:
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=str(destination))
        return destination.exists() and destination.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


_PS_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"""


def _capture_with_powershell(destination: Path) -> bool:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT, str(destination)],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0 and destination.exists() and destination.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False
