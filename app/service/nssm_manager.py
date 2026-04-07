"""
Gerenciamento do serviço Windows via NSSM/SC.

Responsabilidades:
- instalar serviço
- consultar status
- iniciar
- parar
- reiniciar
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from app.core.constants import (
    AUTH_FILE,
    INSTALL_SERVICE_BAT,
    SERVICE_NAME,
    TOOLS_DIR,
)
from app.core.json_store import load_json


@dataclass
class ServiceStatus:
    installed: bool
    running: bool
    state: str
    details: str


def get_nssm_path() -> Path:
    """
    Caminho padrão do NSSM no worker.
    """
    return TOOLS_DIR / "nssm.exe"


def download_nssm() -> tuple[bool, str]:
    """
    Baixa o NSSM da API pública e salva na pasta tools.
    """
    try:
        auth_data = load_json(AUTH_FILE)

        if not isinstance(auth_data, dict):
            return False, f"Conteúdo inválido no arquivo de autenticação: {AUTH_FILE}"

        base_url = str(auth_data.get("base_url") or "").strip()
        if not base_url:
            return False, "base_url não encontrada no AUTH_FILE"

        download_url = f"{base_url.rstrip('/')}/api/v1/public/downloads/nssm.exe"

        target_path = get_nssm_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        response = requests.get(download_url, timeout=60)
        response.raise_for_status()

        target_path.write_bytes(response.content)

        if not target_path.exists():
            return False, f"Falha ao salvar o NSSM em {target_path}"

        return True, f"NSSM baixado com sucesso em {target_path}"

    except Exception as e:
        return False, f"Erro ao baixar NSSM: {e}"


def ensure_nssm() -> tuple[bool, str]:
    """
    Garante que o NSSM existe localmente.
    Se não existir, tenta baixar da API pública.
    """
    local = get_nssm_path()

    if local.exists():
        return True, f"NSSM já disponível em {local}"

    return download_nssm()


def is_nssm_available() -> bool:
    """
    Verifica se o NSSM existe na pasta esperada
    ou no PATH.
    """
    local = get_nssm_path()
    if local.exists():
        return True

    return shutil.which("nssm") is not None


def get_service_status(service_name: str = SERVICE_NAME) -> ServiceStatus:
    """
    Consulta o status do serviço usando SC.
    """
    result = subprocess.run(
        ["sc", "query", service_name],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    normalized = output.upper()

    if "FAILED 1060" in normalized or "DOES NOT EXIST" in normalized:
        return ServiceStatus(
            installed=False,
            running=False,
            state="not_installed",
            details=output.strip(),
        )

    if "RUNNING" in normalized:
        return ServiceStatus(
            installed=True,
            running=True,
            state="running",
            details=output.strip(),
        )

    if "STOPPED" in normalized:
        return ServiceStatus(
            installed=True,
            running=False,
            state="stopped",
            details=output.strip(),
        )

    if "START_PENDING" in normalized:
        return ServiceStatus(
            installed=True,
            running=False,
            state="start_pending",
            details=output.strip(),
        )

    if "STOP_PENDING" in normalized:
        return ServiceStatus(
            installed=True,
            running=False,
            state="stop_pending",
            details=output.strip(),
        )

    return ServiceStatus(
        installed=True,
        running=False,
        state="unknown",
        details=output.strip(),
    )


def install_service() -> tuple[bool, str]:
    """
    Executa o BAT de instalação do serviço.
    """
    nssm_ok, nssm_message = ensure_nssm()
    if not nssm_ok:
        return False, nssm_message

    if not INSTALL_SERVICE_BAT.exists():
        return False, f"Arquivo não encontrado: {INSTALL_SERVICE_BAT}"

    result = subprocess.run(
        ["cmd", "/c", str(INSTALL_SERVICE_BAT)],
        capture_output=True,
        text=True,
        shell=False,
    )

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    success = result.returncode == 0

    if nssm_message:
        output = f"{nssm_message}\n\n{output}".strip()

    return success, output.strip()


def start_service(service_name: str = SERVICE_NAME) -> tuple[bool, str]:
    result = subprocess.run(
        ["sc", "start", service_name],
        capture_output=True,
        text=True,
        shell=False,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode == 0, output.strip()


def stop_service(service_name: str = SERVICE_NAME) -> tuple[bool, str]:
    result = subprocess.run(
        ["sc", "stop", service_name],
        capture_output=True,
        text=True,
        shell=False,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode == 0, output.strip()


def restart_service(service_name: str = SERVICE_NAME) -> tuple[bool, str]:
    stop_ok, stop_output = stop_service(service_name)
    start_ok, start_output = start_service(service_name)

    success = start_ok
    output = f"{stop_output}\n\n{start_output}".strip()
    return success, output
