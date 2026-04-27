"""
Fluxo do cadastro do runner.

Fluxo correto:
- login do usuário no sistema
- registro do worker pela rota /api/v1/worker/registration/
- backend devolve o runner_token bruto
- worker salva esse token localmente para autenticar
  sync, heartbeat e tasks
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from app.core.config_models import (
    AuthData,
    BotRegistryItem,
    BotsRegistry,
    RunnerConfigData,
    RunnerData,
)
from app.core.constants import AUTH_FILE, BOTS_REGISTRY_FILE, RUNNER_FILE
from app.core.exceptions import RunnerRegistrationError, ValidationError
from app.core.http_client import HttpClient
from app.core.json_store import save_model
from app.core.machine_info import collect_machine_info
from app.core.paths import create_worker_structure
from app.core.security import protect_text
from app.installer.runtime_setup import install_or_update_worker_runtime
from app.runtime.interactive_worker_scheduler import install_interactive_worker_task
from app.service.service_files import generate_service_files

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


def validate_installer_input(data: InstallerInput) -> None:
    if not data.base_url.strip():
        raise ValidationError("Informe a URL base do sistema.")
    if not data.login.strip():
        raise ValidationError("Informe o login.")
    if not data.password.strip():
        raise ValidationError("Informe a senha.")
    if not data.runner_name.strip():
        raise ValidationError("Informe o nome do runner.")
    if not data.runner_label.strip():
        raise ValidationError("Informe o label do runner.")


def authenticate_user(client: HttpClient, login: str, password: str) -> dict:
    payload = {
        "login": login,
        "password": password,
    }
    return client.post(LOGIN_PATH, payload)


def build_runner_payload(installer_input: InstallerInput, machine: dict) -> dict:
    return {
        "uuid": str(uuid4()),
        "name": installer_input.runner_name,
        "label": installer_input.runner_label,
        "host_name": machine.get("host_name"),
        "ip": machine.get("ip"),
        "os_name": machine.get("os_name"),
        "os_version": machine.get("os_version"),
        "cpu_arch": machine.get("cpu_arch"),
        "memory_total": machine.get("memory_total"),
        "access_remote": installer_input.access_remote,
    }


def register_runner(client: HttpClient, installer_input: InstallerInput, machine: dict) -> dict:
    payload = build_runner_payload(installer_input, machine)
    return client.post(REGISTER_RUNNER_PATH, payload)


def persist_auth_data(
    base_url: str,
    login: str,
    access_token: str,
    refresh_token: str | None,
    token_type: str | None,
) -> None:
    auth = AuthData(
        base_url=base_url.rstrip("/"),
        login=login,
        encrypted_access_token=protect_text(access_token),
        encrypted_refresh_token=protect_text(refresh_token) if refresh_token else None,
        token_type=token_type or "bearer",
        encryption="dpapi_machine",
        saved_at=datetime.now(timezone.utc),
    )
    save_model(AUTH_FILE, auth)


def persist_runner_data(response: dict, installer_input: InstallerInput, machine: dict) -> None:
    runner_token = response.get("token")
    if not runner_token:
        raise RunnerRegistrationError("Backend não retornou token do runner.")

    runner_id = response.get("runner_id")
    runner_uuid = response.get("uuid")

    if not runner_id or not runner_uuid:
        raise RunnerRegistrationError("Resposta inválida do backend (runner_id/uuid).")

    runner_config = RunnerConfigData(
        max_concurrency=response.get("max_concurrency", 1),
        polling_interval=response.get("polling_interval", 10),
        auto_update_bots=True,
        install_all_bots_on_register=False,
        maintenance_mode=False,
    )

    runner = RunnerData(
        id=runner_id,
        uuid=runner_uuid,
        name=response.get("name", installer_input.runner_name),
        label=installer_input.runner_label,
        host_name=machine.get("host_name", ""),
        ip=machine.get("ip", ""),
        os_name=machine.get("os_name", ""),
        os_version=machine.get("os_version", ""),
        cpu_arch=machine.get("cpu_arch", ""),
        memory_total=machine.get("memory_total", 0),
        access_remote=installer_input.access_remote,
        enabled=response.get("enabled", True),
        status=response.get("status", "offline"),
        token_hash="",
        runner_token=runner_token,
        config=runner_config,
    )

    save_model(RUNNER_FILE, runner)


def persist_bots_registry(response: dict) -> None:
    raw_bots = response.get("bots", [])

    items: list[BotRegistryItem] = []
    for bot in raw_bots:
        bot_id = str(bot.get("bot_id") or bot.get("id") or "")
        if not bot_id:
            continue

        items.append(
            BotRegistryItem(
                bot_id=bot_id,
                name=bot.get("name", ""),
                repository_url=bot.get("repository_url") or bot.get("source_url"),
                entrypoint=bot.get("entrypoint"),
                requirements_file=bot.get("requirements_file"),
                timeout_default=bot.get("timeout_default"),
                expected_version=bot.get("version"),
                expected_commit=bot.get("commit_hash"),
                execution_mode=bot.get("execution_mode", "background"),
            )
        )

    save_model(BOTS_REGISTRY_FILE, BotsRegistry(bots=items))


def run_registration_flow(
    installer_input: InstallerInput,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    def notify(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    validate_installer_input(installer_input)

    notify("Autenticando...")
    client = HttpClient(base_url=installer_input.base_url)

    auth_response = authenticate_user(
        client=client,
        login=installer_input.login,
        password=installer_input.password,
    )

    access_token = auth_response.get("access_token")
    if not access_token:
        raise RunnerRegistrationError("Login não retornou access_token.")

    client.set_token(access_token)

    notify("Coletando dados da máquina...")
    machine = collect_machine_info()

    notify("Registrando runner...")
    register_response = register_runner(client, installer_input, machine)

    notify("Criando estrutura local...")
    create_worker_structure()

    notify("Salvando autenticação...")
    persist_auth_data(
        base_url=installer_input.base_url,
        login=installer_input.login,
        access_token=access_token,
        refresh_token=auth_response.get("refresh_token"),
        token_type=auth_response.get("token_type"),
    )

    notify("Salvando runner...")
    persist_runner_data(register_response, installer_input, machine)

    notify("Salvando bots...")
    persist_bots_registry(register_response)

    notify("Preparando runtime...")
    runtime_result = install_or_update_worker_runtime()

    notify("Gerando arquivos do serviço e do interactive worker...")
    service_files = generate_service_files()

    notify("Instalando e iniciando interactive worker...")
    interactive_worker_ok, interactive_worker_output = install_interactive_worker_task()

    notify("Concluído com sucesso.")

    return {
        "runtime": runtime_result.__dict__,
        "service_files": service_files,
        "interactive_worker_files": {
            "interactive_worker_script": service_files.get("interactive_worker_script"),
            "interactive_worker_vbs": service_files.get("interactive_worker_vbs"),
            "run_interactive_worker_bat": service_files.get("run_interactive_worker_bat"),
            "install_interactive_worker_bat": service_files.get("install_interactive_worker_bat"),
            "remove_interactive_worker_bat": service_files.get("remove_interactive_worker_bat"),
            "diagnostic_interactive_worker_bat": service_files.get("diagnostic_interactive_worker_bat"),
            "interactive_worker_task_name": service_files.get("interactive_worker_task_name"),
        },
        "interactive_worker_install": {
            "success": interactive_worker_ok,
            "output": interactive_worker_output,
        },
    }
