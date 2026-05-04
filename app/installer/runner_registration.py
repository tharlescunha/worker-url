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
    install_or_update_worker_runtime,
    get_worker_runtime_dir,
    get_worker_runtime_venv_dir,
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


def run_registration_flow(
    installer_input: InstallerInput,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:

    def notify(msg: str):
        if progress_callback:
            progress_callback(msg)

    client = HttpClient(base_url=installer_input.base_url)

    notify("Autenticando...")
    auth_response = client.post(
        LOGIN_PATH,
        {
            "login": installer_input.login,
            "password": installer_input.password,
        },
    )

    access_token = auth_response.get("access_token")
    if not access_token:
        raise RuntimeError("Falha no login")

    client.set_token(access_token)

    notify("Coletando dados da máquina...")
    machine = collect_machine_info()

    existing_runner = load_model(RUNNER_FILE, RunnerData)
    runner_uuid = existing_runner.uuid if existing_runner else str(uuid4())

    notify("Registrando runner...")

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

    notify("Criando estrutura local...")
    create_worker_structure()

    notify("Salvando autenticação...")
    auth = AuthData(
        base_url=installer_input.base_url,
        login=installer_input.login,
        encrypted_access_token=protect_text(access_token),
        encrypted_refresh_token=None,
        token_type="bearer",
        encryption="dpapi_machine",
        saved_at=datetime.now(timezone.utc),
    )
    save_model(AUTH_FILE, auth)

    notify("Salvando runner...")
    runner = RunnerData(
        id=response["runner_id"],
        uuid=response["uuid"],
        name=response["name"],
        label=response.get("label") or installer_input.runner_label,
        host_name=machine.get("host_name"),
        ip=machine.get("ip"),
        os_name=machine.get("os_name"),
        os_version=machine.get("os_version"),
        cpu_arch=machine.get("cpu_arch"),
        memory_total=machine.get("memory_total"),
        access_remote=installer_input.access_remote,
        enabled=True,
        status="offline",
        token_hash="",
        runner_token=response["token"],
        config=RunnerConfigData(),
    )

    save_model(RUNNER_FILE, runner)

    notify("Salvando bots...")
    bots = []
    for b in response.get("bots", []):
        bots.append(
            BotRegistryItem(
                bot_id=str(b.get("bot_id")),
                bot_version_id=b.get("bot_version_id"),
                name=b.get("name"),
                repository_url=b.get("repository_url"),
                entrypoint=b.get("entrypoint"),
                branch=b.get("branch"),
                expected_version=b.get("version"),
                expected_commit=b.get("commit_hash"),
                execution_mode=b.get("execution_mode", "background"),
            )
        )

    save_model(BOTS_REGISTRY_FILE, BotsRegistry(bots=bots))

    notify("Preparando runtime...")
    install_or_update_worker_runtime()

    notify("Gerando BAT...")

    runtime_dir = get_worker_runtime_dir()
    python_exe = get_worker_runtime_venv_dir() / "Scripts" / "python.exe"

    content = f"""@echo off
cd /d "{runtime_dir}"
"{python_exe}" -m app.runtime.main
pause
"""

    WORKER_BAT_FILE.write_text(content, encoding="utf-8")

    notify("Criando atalho...")

    desktop = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    shortcut = desktop / "OrkaFlow Worker.lnk"

    subprocess.run(
        [
            "powershell",
            "-Command",
            f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut}")
$Shortcut.TargetPath = "{WORKER_BAT_FILE}"
$Shortcut.Save()
""",
        ],
        shell=True,
    )

    notify("Finalizado.")

    return {
        "worker_bat": str(WORKER_BAT_FILE),
        "shortcut": str(shortcut),
    }
