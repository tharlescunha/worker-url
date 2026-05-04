from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.core.config_models import (
    AuthData,
    BotRegistryItem,
    BotsRegistry,
    RunnerConfigData,
    RunnerData,
)
from app.core.constants import (
    AUTH_FILE,
    BOTS_REGISTRY_FILE,
    RUNNER_FILE,
    WORKER_BAT_FILE,
)
from app.core.http_client import HttpClient
from app.core.json_store import load_model, save_model
from app.core.machine_info import collect_machine_info
from app.core.paths import create_worker_structure
from app.core.security import protect_text
from app.installer.runtime_setup import (
    get_worker_runtime_dir,
    get_worker_runtime_venv_dir,
    install_or_update_worker_runtime,
)

LOGIN_PATH = "/api/v1/auth/login"
REGISTER_RUNNER_PATH = "/api/v1/worker/registration/"


@dataclass
class InstallerInput:
    base_url: str
    login: str
    password: str
    runner_name: str
    runner_label: str
    access_remote: bool = False


def _notify(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback:
        progress_callback(message)


def _resolve_desktop_dir() -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        raise RuntimeError("USERPROFILE não encontrado para criar atalho.")

    candidates = [
        Path(user_profile) / "Desktop",
        Path(user_profile) / "Área de Trabalho",
        Path(user_profile) / "OneDrive" / "Desktop",
        Path(user_profile) / "OneDrive" / "Área de Trabalho",
    ]

    for path in candidates:
        if path.exists():
            return path

    fallback = Path(user_profile) / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def create_worker_bat() -> str:
    runtime_dir = get_worker_runtime_dir()
    python_exe = get_worker_runtime_venv_dir() / "Scripts" / "python.exe"

    if not runtime_dir.exists():
        raise RuntimeError(f"Pasta do runtime não encontrada: {runtime_dir}")

    if not python_exe.exists():
        raise RuntimeError(f"Python do runtime não encontrado: {python_exe}")

    WORKER_BAT_FILE.parent.mkdir(parents=True, exist_ok=True)

    content = f"""@echo off
title OrkaFlow Worker
cd /d "{runtime_dir}"

echo ========================================
echo          ORKAFLOW WORKER
echo ========================================
echo.
echo Worker iniciado.
echo Deixe esta janela aberta.
echo.
echo Pressione CTRL+C para parar.
echo.

"{python_exe}" -m app.runtime.main

echo.
echo Worker finalizado.
pause
"""

    WORKER_BAT_FILE.write_text(content, encoding="utf-8")
    return str(WORKER_BAT_FILE)


def create_desktop_shortcut() -> str:
    desktop = _resolve_desktop_dir()
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
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        capture_output=True,
        text=True,
        shell=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Falha ao criar atalho: {(result.stderr or result.stdout or '').strip()}"
        )

    if not shortcut.exists():
        raise RuntimeError(f"Atalho não foi criado: {shortcut}")

    return str(shortcut)


def run_registration_flow(
    installer_input: InstallerInput,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    client = HttpClient(base_url=installer_input.base_url)

    _notify(progress_callback, "Autenticando...")
    auth_response = client.post(
        LOGIN_PATH,
        {
            "login": installer_input.login,
            "password": installer_input.password,
        },
    )

    access_token = auth_response.get("access_token")
    if not access_token:
        raise RuntimeError("Falha no login: access_token não retornado.")

    client.set_token(access_token)

    _notify(progress_callback, "Coletando dados da máquina...")
    machine = collect_machine_info()

    existing_runner = load_model(RUNNER_FILE, RunnerData)
    runner_uuid = existing_runner.uuid if existing_runner else str(uuid4())

    _notify(progress_callback, "Registrando ou atualizando runner...")
    response = client.post(
        REGISTER_RUNNER_PATH,
        {
            "uuid": runner_uuid,
            "name": installer_input.runner_name,
            "label": installer_input.runner_label,
            "host_name": machine.get("host_name"),
            "ip": machine.get("ip"),
            "os_name": machine.get("os_name"),
            "os_version": machine.get("os_version"),
            "cpu_arch": machine.get("cpu_arch"),
            "memory_total": machine.get("memory_total"),
            "access_remote": installer_input.access_remote,
        },
    )

    _notify(progress_callback, "Criando estrutura local...")
    create_worker_structure()

    _notify(progress_callback, "Salvando autenticação...")
    auth = AuthData(
        base_url=installer_input.base_url.rstrip("/"),
        login=installer_input.login,
        encrypted_access_token=protect_text(access_token),
        encrypted_refresh_token=protect_text(auth_response.get("refresh_token"))
        if auth_response.get("refresh_token")
        else None,
        token_type=auth_response.get("token_type") or "bearer",
        encryption="dpapi_machine",
        saved_at=datetime.now(timezone.utc),
    )
    save_model(AUTH_FILE, auth)

    _notify(progress_callback, "Salvando runner...")
    runner_config = RunnerConfigData(
        max_concurrency=response.get("max_concurrency", 1),
        polling_interval=response.get("polling_interval", 10),
        auto_update_bots=True,
        install_all_bots_on_register=False,
        maintenance_mode=False,
    )

    runner = RunnerData(
        id=response["runner_id"],
        uuid=response["uuid"],
        name=response.get("name") or installer_input.runner_name,
        label=response.get("label") or installer_input.runner_label,
        host_name=machine.get("host_name") or "",
        ip=machine.get("ip") or "",
        os_name=machine.get("os_name") or "",
        os_version=machine.get("os_version") or "",
        cpu_arch=machine.get("cpu_arch") or "",
        memory_total=machine.get("memory_total") or 0,
        access_remote=installer_input.access_remote,
        enabled=response.get("enabled", True),
        status=response.get("status", "offline"),
        token_hash="",
        runner_token=response["token"],
        config=runner_config,
    )
    save_model(RUNNER_FILE, runner)

    _notify(progress_callback, "Salvando bots...")
    bots: list[BotRegistryItem] = []

    for bot in response.get("bots", []):
        bot_id = str(bot.get("bot_id") or bot.get("id") or "").strip()
        if not bot_id:
            continue

        bots.append(
            BotRegistryItem(
                bot_id=bot_id,
                bot_version_id=bot.get("bot_version_id"),
                name=bot.get("name") or "",
                technology=bot.get("technology"),
                source_type=bot.get("source_type"),
                repository_url=bot.get("repository_url") or bot.get("source_url"),
                artifact_path=bot.get("artifact_path"),
                branch=bot.get("branch"),
                entrypoint=bot.get("entrypoint"),
                requirements_file=bot.get("requirements_file"),
                timeout_default=bot.get("timeout_default"),
                checksum=bot.get("checksum"),
                expected_version=bot.get("version"),
                expected_commit=bot.get("commit_hash"),
                execution_mode=bot.get("execution_mode") or "background",
                linked=True,
            )
        )

    save_model(BOTS_REGISTRY_FILE, BotsRegistry(bots=bots))

    _notify(progress_callback, "Preparando runtime...")
    runtime_result = install_or_update_worker_runtime()

    _notify(progress_callback, "Gerando BAT do worker...")
    worker_bat = create_worker_bat()

    _notify(progress_callback, "Criando atalho na área de trabalho...")
    desktop_shortcut = create_desktop_shortcut()

    _notify(progress_callback, "Finalizado.")

    return {
        "runtime": runtime_result.__dict__,
        "worker_bat": worker_bat,
        "desktop_shortcut": desktop_shortcut,
    }
