"""Coloca o pacote `maestro` (biblioteca do robô) no venv de cada bot.

Por cópia direta em site-packages, não por pip: não precisa de rede nem de setuptools no venv,
e nunca falha a instalação do bot (qualquer problema vira aviso). A fonte é a pasta `maestro/`
do próprio repositório do worker, então a versão nas máquinas é sempre a que o worker carrega.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]

# <repo do worker>/maestro  (este arquivo fica em <repo>/app/sync/)
MAESTRO_SOURCE_DIR = Path(__file__).resolve().parents[2] / "maestro"
MARKER_FILE_NAME = "maestro.installed"


def maestro_version() -> str | None:
    init_file = MAESTRO_SOURCE_DIR / "maestro" / "core.py"
    try:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def site_packages_dir(venv_dir: Path) -> Path | None:
    candidates = [venv_dir / "Lib" / "site-packages"]
    lib = venv_dir / "lib"
    if lib.exists():
        candidates.extend(sorted(lib.glob("python*/site-packages")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def installed_version(venv_dir: Path) -> str | None:
    marker = venv_dir / MARKER_FILE_NAME
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def ensure_maestro_in_venv(
    venv_dir: Path | str,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> bool:
    """Garante o maestro no venv. Devolve True se instalou/atualizou, False se já estava ou não deu."""
    venv_dir = Path(venv_dir)
    source_pkg = MAESTRO_SOURCE_DIR / "maestro"
    version = maestro_version()
    if version is None or not (source_pkg / "__init__.py").exists():
        return False  # worker sem a pasta maestro (checkout antigo): nada a fazer
    if installed_version(venv_dir) == version:
        return False

    target_root = site_packages_dir(venv_dir)
    if target_root is None:
        return False

    target = target_root / "maestro"
    try:
        tmp_target = target_root / "maestro.__novo__"
        if tmp_target.exists():
            shutil.rmtree(tmp_target, ignore_errors=True)
        shutil.copytree(source_pkg, tmp_target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        tmp_target.rename(target)
        (venv_dir / MARKER_FILE_NAME).write_text(version, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        if progress_callback:
            progress_callback(f"Aviso: não foi possível instalar o maestro no venv do bot {bot_label}: {exc}")
        return False

    if progress_callback:
        progress_callback(f"maestro {version} instalado no venv do bot {bot_label}.")
    return True
