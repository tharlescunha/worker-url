from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config_models import BotRegistryItem
from app.core.constants import BOTS_DIR, VENVS_DIR
from app.core.exceptions import BotInstallError


@dataclass
class InstallResult:
    local_path: str
    venv_path: str
    installed_commit: str | None
    requirements_hash: str | None
    message: str


def install_or_update_bot(bot: BotRegistryItem) -> InstallResult:
    source_url = _resolve_source_url(bot)
    bot_dir = BOTS_DIR / f"bot_{bot.bot_id}"
    venv_dir = VENVS_DIR / f"bot_{bot.bot_id}"

    _ensure_git_available()
    _prepare_repository(bot_dir=bot_dir, source_url=source_url)
    _checkout_expected_revision(bot=bot, bot_dir=bot_dir)
    installed_commit = _get_current_commit(bot_dir)

    venv_python = _ensure_venv(venv_dir)
    requirements_hash = _install_requirements(
        bot=bot,
        bot_dir=bot_dir,
        venv_python=venv_python,
    )

    message = (
        f"Bot preparado com sucesso. "
        f"repo={bot_dir} venv={venv_dir} commit={installed_commit or 'desconhecido'}"
    )

    return InstallResult(
        local_path=str(bot_dir),
        venv_path=str(venv_dir),
        installed_commit=installed_commit,
        requirements_hash=requirements_hash,
        message=message,
    )


def _resolve_source_url(bot: BotRegistryItem) -> str:
    source_type = (bot.source_type or "").strip().lower()

    if source_type in ("git", "") and bot.repository_url:
        return bot.repository_url

    if bot.repository_url:
        return bot.repository_url

    raise BotInstallError(
        f"Bot {bot.bot_id} sem repository_url para instalação via Git."
    )


def _ensure_git_available() -> None:
    if shutil.which("git") is None:
        raise BotInstallError("Git não encontrado no PATH da máquina.")


def _prepare_repository(bot_dir: Path, source_url: str) -> None:
    if not bot_dir.exists():
        bot_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_command(
            ["git", "clone", source_url, str(bot_dir)],
            cwd=None,
            error_prefix=f"Falha ao clonar repositório {source_url}",
        )
        return

    if not (bot_dir / ".git").exists():
        raise BotInstallError(
            f"A pasta do bot existe mas não é um repositório Git válido: {bot_dir}"
        )

    _run_command(
        ["git", "remote", "set-url", "origin", source_url],
        cwd=bot_dir,
        error_prefix="Falha ao atualizar remote origin",
    )

    _run_command(
        ["git", "fetch", "--all", "--tags", "--prune"],
        cwd=bot_dir,
        error_prefix="Falha ao fazer git fetch",
    )


def _checkout_expected_revision(bot: BotRegistryItem, bot_dir: Path) -> None:
    expected_commit = (bot.expected_commit or "").strip()
    branch = (bot.branch or "").strip()

    if expected_commit:
        _run_command(
            ["git", "checkout", "--force", expected_commit],
            cwd=bot_dir,
            error_prefix=f"Falha ao fazer checkout do commit {expected_commit}",
        )
        return

    if branch:
        _run_command(
            ["git", "checkout", "--force", branch],
            cwd=bot_dir,
            error_prefix=f"Falha ao fazer checkout da branch {branch}",
        )
        _run_command(
            ["git", "pull", "origin", branch],
            cwd=bot_dir,
            error_prefix=f"Falha ao atualizar branch {branch}",
        )
        return

    _run_command(
        ["git", "pull"],
        cwd=bot_dir,
        error_prefix="Falha ao atualizar repositório",
    )


def _get_current_commit(bot_dir: Path) -> str | None:
    result = _run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=bot_dir,
        error_prefix="Falha ao obter commit atual",
    )
    commit = (result.stdout or "").strip()
    return commit or None


def _ensure_venv(venv_dir: Path) -> Path:
    venv_python = _venv_python(venv_dir)

    if not venv_python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_command(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=None,
            error_prefix=f"Falha ao criar venv do bot em {venv_dir}",
        )

    if not venv_python.exists():
        raise BotInstallError(f"Venv criada, mas python não encontrado em {venv_python}")

    _run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=None,
        error_prefix="Falha ao atualizar pip da venv",
    )

    return venv_python


def _install_requirements(bot: BotRegistryItem, bot_dir: Path, venv_python: Path) -> str | None:
    requirements_name = (bot.requirements_file or "requirements.txt").strip()
    requirements_path = bot_dir / requirements_name

    if not requirements_path.exists():
        return None

    _run_command(
        [str(venv_python), "-m", "pip", "install", "-r", str(requirements_path)],
        cwd=bot_dir,
        error_prefix=f"Falha ao instalar requirements do bot {bot.bot_id}",
    )

    return _sha256_file(requirements_path)


def _sha256_file(file_path: Path) -> str:
    sha = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe"


def _run_command(command: list[str], cwd: Path | None, error_prefix: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
    except Exception as exc:
        raise BotInstallError(f"{error_prefix}: {exc}") from exc

    if result.returncode != 0:
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        detail = stderr or stdout or "sem detalhes"
        raise BotInstallError(f"{error_prefix}: {detail}")

    return result
