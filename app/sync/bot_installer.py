from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.config_models import BotRegistryItem
from app.core.constants import BOTS_DIR, VENVS_DIR
from app.core.exceptions import BotInstallError


ProgressCallback = Callable[[str], None]

DEFAULT_COMMAND_TIMEOUT_SECONDS = 300


@dataclass
class InstallResult:
    local_path: str
    venv_path: str
    installed_commit: str | None
    requirements_hash: str | None
    message: str


def install_or_update_bot(
    bot: BotRegistryItem,
    progress_callback: ProgressCallback | None = None,
) -> InstallResult:
    bot_label = _bot_label(bot)
    _notify(progress_callback, f"Preparando bot {bot_label}.")

    source_url = _resolve_source_url(bot)
    bot_dir, venv_dir = resolve_bot_install_paths(bot)

    _notify(
        progress_callback,
        f"Pastas do bot {bot_label}: repo={bot_dir} venv={venv_dir}",
    )

    _ensure_git_available()
    _notify(progress_callback, "Git encontrado no PATH.")

    _prepare_repository(
        bot_dir=bot_dir,
        source_url=source_url,
        progress_callback=progress_callback,
        bot_label=bot_label,
    )
    _checkout_expected_revision(
        bot=bot,
        bot_dir=bot_dir,
        progress_callback=progress_callback,
        bot_label=bot_label,
    )
    _sync_submodules(
        bot_dir,
        progress_callback=progress_callback,
        bot_label=bot_label,
    )

    installed_commit = _get_current_commit(bot_dir)
    _notify(
        progress_callback,
        f"Commit atual do bot {bot_label}: {installed_commit or 'desconhecido'}",
    )

    venv_python = _ensure_venv(
        venv_dir,
        progress_callback=progress_callback,
        bot_label=bot_label,
    )
    requirements_hash = _install_requirements(
        bot=bot,
        bot_dir=bot_dir,
        venv_python=venv_python,
        progress_callback=progress_callback,
        bot_label=bot_label,
    )

    message = (
        f"Bot preparado com sucesso. "
        f"repo={bot_dir} venv={venv_dir} commit={installed_commit or 'desconhecido'}"
    )
    _notify(progress_callback, message)

    return InstallResult(
        local_path=str(bot_dir),
        venv_path=str(venv_dir),
        installed_commit=installed_commit,
        requirements_hash=requirements_hash,
        message=message,
    )


def resolve_bot_install_paths(bot: BotRegistryItem) -> tuple[Path, Path]:
    folder_name = _safe_folder_name(bot.name or f"bot_{bot.bot_id}")
    return BOTS_DIR / folder_name, VENVS_DIR / folder_name


def _notify(progress_callback: ProgressCallback | None, message: str) -> None:
    if progress_callback:
        progress_callback(message)


def _bot_label(bot: BotRegistryItem) -> str:
    name = (bot.name or "").strip()
    if name:
        return f"{name} ({bot.bot_id})"
    return f"bot_{bot.bot_id}"


def _safe_folder_name(value: str) -> str:
    invalid_chars = '<>:"/\\|?*'
    cleaned = "".join(
        "_" if char in invalid_chars or ord(char) < 32 else char
        for char in value.strip()
    )
    cleaned = " ".join(cleaned.split()).strip(" .")

    if not cleaned:
        cleaned = "bot"

    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if cleaned.upper() in reserved_names:
        cleaned = f"{cleaned}_bot"

    return cleaned[:120].strip(" .") or "bot"


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


def _prepare_repository(
    bot_dir: Path,
    source_url: str,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> None:
    if not bot_dir.exists():
        bot_dir.parent.mkdir(parents=True, exist_ok=True)
        _notify(progress_callback, f"Clonando repositorio do bot {bot_label}.")
        _run_command(
            ["git", "clone", "--recursive", source_url, str(bot_dir)],
            cwd=None,
            error_prefix=f"Falha ao clonar repositório {source_url}",
        )
        return

    if not (bot_dir / ".git").exists():
        raise BotInstallError(
            f"A pasta do bot existe mas não é um repositório Git válido: {bot_dir}"
        )

    _notify(progress_callback, f"Atualizando remote origin do bot {bot_label}.")
    _run_command(
        ["git", "remote", "set-url", "origin", source_url],
        cwd=bot_dir,
        error_prefix="Falha ao atualizar remote origin",
    )

    _notify(progress_callback, f"Buscando atualizacoes do bot {bot_label}.")
    _run_command(
        ["git", "fetch", "--all", "--tags", "--prune"],
        cwd=bot_dir,
        error_prefix="Falha ao fazer git fetch",
    )


def _checkout_expected_revision(
    bot: BotRegistryItem,
    bot_dir: Path,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> None:
    expected_commit = (bot.expected_commit or "").strip()
    branch = (bot.branch or "").strip()

    if expected_commit:
        _notify(
            progress_callback,
            f"Fazendo checkout do commit esperado do bot {bot_label}.",
        )
        _run_command(
            ["git", "checkout", "--force", expected_commit],
            cwd=bot_dir,
            error_prefix=f"Falha ao fazer checkout do commit {expected_commit}",
        )
        _force_clean_worktree(
            bot_dir,
            target_ref=expected_commit,
            progress_callback=progress_callback,
            bot_label=bot_label,
        )
        return

    if branch:
        _notify(progress_callback, f"Fazendo checkout da branch {branch} do bot {bot_label}.")
        _run_command(
            ["git", "fetch", "origin", branch],
            cwd=bot_dir,
            error_prefix=f"Falha ao buscar branch {branch}",
        )

        _notify(progress_callback, f"Buscando branch {branch} do bot {bot_label}.")
        _run_command(
            ["git", "checkout", "-B", branch, f"origin/{branch}"],
            cwd=bot_dir,
            error_prefix=f"Falha ao recriar branch local {branch}",
        )

        _notify(progress_callback, f"Alinhando branch {branch} do bot {bot_label}.")
        _run_command(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=bot_dir,
            error_prefix=f"Falha ao resetar branch {branch}",
        )

        _notify(progress_callback, f"Limpando arquivos locais do bot {bot_label}.")
        _run_command(
            ["git", "clean", "-ffdx"],
            cwd=bot_dir,
            error_prefix=f"Falha ao limpar arquivos locais da branch {branch}",
        )
        return

    default_branch = _resolve_remote_default_branch(bot_dir)
    _notify(progress_callback, f"Forcando branch padrao {default_branch} do bot {bot_label}.")
    _run_command(
        ["git", "checkout", "-B", default_branch, f"origin/{default_branch}"],
        cwd=bot_dir,
        error_prefix=f"Falha ao recriar branch local {default_branch}",
    )
    _run_command(
        ["git", "reset", "--hard", f"origin/{default_branch}"],
        cwd=bot_dir,
        error_prefix=f"Falha ao resetar branch {default_branch}",
    )
    _run_command(
        ["git", "clean", "-ffdx"],
        cwd=bot_dir,
        error_prefix=f"Falha ao limpar arquivos locais da branch {default_branch}",
    )
    return

    _notify(progress_callback, f"Atualizando repositorio do bot {bot_label}.")
    _run_command(
        ["git", "status", "--short"],
        cwd=bot_dir,
        error_prefix="Falha ao atualizar repositório",
    )


def _force_clean_worktree(
    bot_dir: Path,
    target_ref: str,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> None:
    _notify(progress_callback, f"Resetando bot {bot_label} para {target_ref}.")
    _run_command(
        ["git", "reset", "--hard", target_ref],
        cwd=bot_dir,
        error_prefix=f"Falha ao resetar repositorio para {target_ref}",
    )

    _notify(progress_callback, f"Removendo alteracoes e arquivos locais do bot {bot_label}.")
    _run_command(
        ["git", "clean", "-ffdx"],
        cwd=bot_dir,
        error_prefix="Falha ao limpar arquivos locais do repositorio",
    )


def _resolve_remote_default_branch(bot_dir: Path) -> str:
    try:
        result = _run_command(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=bot_dir,
            error_prefix="Falha ao identificar branch padrao remota",
        )
    except BotInstallError:
        _run_command(
            ["git", "remote", "set-head", "origin", "--auto"],
            cwd=bot_dir,
            error_prefix="Falha ao atualizar referencia origin/HEAD",
        )
        result = _run_command(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=bot_dir,
            error_prefix="Falha ao identificar branch padrao remota",
        )
    ref = (result.stdout or "").strip()
    if ref.startswith("origin/"):
        ref = ref.split("/", 1)[1]

    if not ref:
        raise BotInstallError("Branch padrao remota nao identificada para o bot.")

    return ref


def _sync_submodules(
    bot_dir: Path,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> None:
    gitmodules = bot_dir / ".gitmodules"
    if not gitmodules.exists():
        return

    _notify(progress_callback, f"Sincronizando submodules do bot {bot_label}.")
    _run_command(
        ["git", "submodule", "sync", "--recursive"],
        cwd=bot_dir,
        error_prefix="Falha ao sincronizar submodules",
    )

    _run_command(
        ["git", "submodule", "update", "--init", "--recursive", "--force"],
        cwd=bot_dir,
        error_prefix="Falha ao atualizar submodules",
    )


def git_pull_for_auto_update(
    bot_dir: Path,
    branch: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> str | None:
    """Faz fetch + reset para o HEAD remoto e retorna o novo commit hash."""
    if not bot_dir.exists() or not (bot_dir / ".git").exists():
        return None

    _ensure_git_available()

    _run_command(
        ["git", "fetch", "--all", "--tags", "--prune"],
        cwd=bot_dir,
        error_prefix="auto_update: falha ao buscar atualizacoes",
    )

    target_branch = branch or _resolve_remote_default_branch(bot_dir)

    _run_command(
        ["git", "checkout", "-B", target_branch, f"origin/{target_branch}"],
        cwd=bot_dir,
        error_prefix=f"auto_update: falha ao fazer checkout da branch {target_branch}",
    )
    _run_command(
        ["git", "reset", "--hard", f"origin/{target_branch}"],
        cwd=bot_dir,
        error_prefix=f"auto_update: falha ao resetar para origin/{target_branch}",
    )

    _notify(progress_callback, f"auto_update: branch {target_branch} atualizada.")
    return _get_current_commit(bot_dir)


def _get_current_commit(bot_dir: Path) -> str | None:
    result = _run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=bot_dir,
        error_prefix="Falha ao obter commit atual",
    )
    commit = (result.stdout or "").strip()
    return commit or None


def _ensure_venv(
    venv_dir: Path,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> Path:
    venv_python = _venv_python(venv_dir)

    if not venv_python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        _notify(progress_callback, f"Criando venv do bot {bot_label}.")
        _run_command(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=None,
            error_prefix=f"Falha ao criar venv do bot em {venv_dir}",
        )

    if not venv_python.exists():
        raise BotInstallError(f"Venv criada, mas python não encontrado em {venv_python}")

    _notify(progress_callback, f"Atualizando pip da venv do bot {bot_label}.")
    _run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=None,
        error_prefix="Falha ao atualizar pip da venv",
    )

    return venv_python


def _install_requirements(
    bot: BotRegistryItem,
    bot_dir: Path,
    venv_python: Path,
    progress_callback: ProgressCallback | None = None,
    bot_label: str = "bot",
) -> str | None:
    requirements_name = (bot.requirements_file or "requirements.txt").strip()
    requirements_path = bot_dir / requirements_name

    if not requirements_path.exists():
        _notify(
            progress_callback,
            f"Requirements nao encontrado para o bot {bot_label}: {requirements_name}",
        )
        return None

    _notify(progress_callback, f"Instalando requirements do bot {bot_label}.")
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


def _run_command(
    command: list[str],
    cwd: Path | None,
    error_prefix: str,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BotInstallError(
            f"{error_prefix}: comando excedeu {timeout_seconds}s e foi encerrado."
        ) from exc
    except Exception as exc:
        raise BotInstallError(f"{error_prefix}: {exc}") from exc

    if result.returncode != 0:
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        detail = stderr or stdout or "sem detalhes"
        raise BotInstallError(f"{error_prefix}: {detail}")

    return result
