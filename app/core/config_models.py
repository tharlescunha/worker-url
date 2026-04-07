"""
Models dos arquivos locais.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AuthData(BaseModel):
    base_url: str
    login: str
    encrypted_access_token: str
    encrypted_refresh_token: str | None = None
    token_type: str = "bearer"
    encryption: str = "dpapi_machine"
    saved_at: datetime | None = None


class RunnerConfigData(BaseModel):
    max_concurrency: int = Field(default=1, ge=1)
    allowed_parallel_bots: dict = Field(default_factory=dict)
    polling_interval: int = Field(default=15, ge=5)
    auto_update_bots: bool = True
    install_all_bots_on_register: bool = False
    maintenance_mode: bool = False


class RunnerData(BaseModel):
    id: int
    uuid: str
    name: str
    label: str
    host_name: str
    ip: str
    os_name: str
    os_version: str
    cpu_arch: str
    memory_total: int
    access_remote: bool = False
    enabled: bool = True
    status: str = "offline"
    token_hash: str
    runner_token: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_heartbeat: datetime | None = None
    config: RunnerConfigData


class BotRegistryItem(BaseModel):
    bot_id: str
    bot_version_id: int | None = None
    name: str = ""

    technology: str | None = None
    source_type: str | None = None

    repository_url: str | None = None
    artifact_path: str | None = None
    branch: str | None = None
    entrypoint: str | None = None
    requirements_file: str | None = None
    timeout_default: int | None = None
    checksum: str | None = None

    expected_version: str | None = None
    expected_commit: str | None = None

    local_path: str = ""
    venv_path: str = ""
    installed_version: str | None = None
    installed_commit: str | None = None

    last_sync_at: datetime | None = None
    requirements_hash: str | None = None
    last_install_status: str | None = None
    last_install_message: str | None = None
    linked: bool = True


class BotsRegistry(BaseModel):
    bots: list[BotRegistryItem] = Field(default_factory=list)


class WorkerServiceConfig(BaseModel):
    service_name: str
    display_name: str
    description: str
    project_root: str
    working_directory: str
    python_executable: str
    runtime_module: str
    auth_file: str
    runner_file: str
    logs_dir: str
    command: str
    command_args: str
    install_hint: str
    created_at: datetime | None = None
    