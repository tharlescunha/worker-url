from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config_models import AuthData, RunnerData
from app.core.constants import AUTH_FILE, BOTS_DIR, RUNNER_FILE, VENVS_DIR
from app.core.exceptions import BotInstallError
from app.core.http_client import HttpClient
from app.core.json_store import load_model
from app.core.security import unprotect_text
from app.sync.bot_installer import (
    _ensure_git_available,
    _get_current_commit,
    _install_requirements,
    _prepare_repository,
    _run_command,
    _venv_python,
)

WORKER_RUNTIME_NAME = "worker-runtime"
WORKER_RUNTIME_BRANCH = "main"
WORKER_RUNTIME_PARAMETER_KEY = "worker-url"
WORKER_RUNTIME_PARAMETER_PATH = f"/api/v1/worker/parameters/{WORKER_RUNTIME_PARAMETER_KEY}"


@dataclass
class WorkerRuntimeSetupResult:
    local_path: str
    venv_path: str
    installed_commit: str | None
    requirements_hash: str | None
    message: str


def get_worker_runtime_dir() -> Path:
    return BOTS_DIR / WORKER_RUNTIME_NAME


def get_worker_runtime_venv_dir() -> Path:
    return VENVS_DIR / WORKER_RUNTIME_NAME


def install_or_update_worker_runtime() -> WorkerRuntimeSetupResult:
    runtime_repository_url = _resolve_worker_runtime_repository_url()

    runtime_dir = get_worker_runtime_dir()
    venv_dir = get_worker_runtime_venv_dir()

    _ensure_git_available()

    _prepare_repository(
        bot_dir=runtime_dir,
        source_url=runtime_repository_url,
    )

    _force_update_runtime_repository(
        runtime_dir=runtime_dir,
        branch=WORKER_RUNTIME_BRANCH.strip() or "main",
    )

    installed_commit = _get_current_commit(runtime_dir)

    venv_python = _ensure_runtime_venv(venv_dir)
    requirements_hash = _install_runtime_requirements(runtime_dir, venv_python)

    message = (
        f"Runtime preparado com sucesso. "
        f"repo={runtime_dir} venv={venv_dir} commit={installed_commit or 'desconhecido'}"
    )

    return WorkerRuntimeSetupResult(
        local_path=str(runtime_dir),
        venv_path=str(venv_dir),
        installed_commit=installed_commit,
        requirements_hash=requirements_hash,
        message=message,
    )


def _resolve_worker_runtime_repository_url() -> str:
    auth = load_model(AUTH_FILE, AuthData)
    runner = load_model(RUNNER_FILE, RunnerData)

    if not auth:
        raise BotInstallError(
            f"Arquivo de autenticação não encontrado ou inválido: {AUTH_FILE}"
        )

    if not runner:
        raise BotInstallError(
            f"Arquivo do runner não encontrado ou inválido: {RUNNER_FILE}"
        )

    access_token = unprotect_text(auth.encrypted_access_token)
    if not access_token:
        raise BotInstallError("Não foi possível descriptografar o access_token do worker.")

    client = HttpClient(base_url=auth.base_url)
    client.set_token(access_token)

    response = client.post(
        WORKER_RUNTIME_PARAMETER_PATH,
        {
            "uuid": runner.uuid,
            "token": runner.runner_token,
        },
    )

    runtime_url = str(response.get("value") or "").strip()
    if not runtime_url:
        raise BotInstallError(
            f"A API não retornou um valor válido para o parâmetro '{WORKER_RUNTIME_PARAMETER_KEY}'."
        )

    return runtime_url


def _force_update_runtime_repository(runtime_dir: Path, branch: str) -> None:
    _run_command(
        ["git", "checkout", "--force", branch],
        cwd=runtime_dir,
        error_prefix=f"Falha ao fazer checkout da branch {branch} do runtime",
    )

    _run_command(
        ["git", "fetch", "--all", "--tags", "--prune"],
        cwd=runtime_dir,
        error_prefix="Falha ao fazer git fetch do runtime",
    )

    _run_command(
        ["git", "reset", "--hard", f"origin/{branch}"],
        cwd=runtime_dir,
        error_prefix=f"Falha ao fazer reset hard para origin/{branch}",
    )

    _run_command(
        ["git", "clean", "-fd"],
        cwd=runtime_dir,
        error_prefix="Falha ao limpar arquivos locais do runtime",
    )


def _ensure_runtime_venv(venv_dir: Path) -> Path:
    venv_python = _venv_python(venv_dir)

    if not venv_python.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        _run_command(
            ["python", "-m", "venv", str(venv_dir)],
            cwd=None,
            error_prefix=f"Falha ao criar venv do runtime em {venv_dir}",
        )

    if not venv_python.exists():
        raise BotInstallError(
            f"Venv do runtime criada, mas python não encontrado em {venv_python}"
        )

    _run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=None,
        error_prefix="Falha ao atualizar pip da venv do runtime",
    )

    return venv_python


def _install_runtime_requirements(runtime_dir: Path, venv_python: Path) -> str | None:
    return _install_requirements(
        bot=type(
            "RuntimeRequirements",
            (),
            {"bot_id": "worker-runtime", "requirements_file": "requirements.txt"},
        )(),
        bot_dir=runtime_dir,
        venv_python=venv_python,
    )
