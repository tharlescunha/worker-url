from __future__ import annotations

import ctypes
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.constants import (
    INTERACTIVE_AGENT_HEARTBEAT_TTL_SECONDS,
    INTERACTIVE_AGENT_STATE_FILE,
)
from app.core.json_store import load_json


WTS_CURRENT_SERVER_HANDLE = 0
WTSActive = 0


@dataclass
class InteractiveAgentState:
    is_active: bool
    pid: int | None = None
    session_id: int | None = None
    username: str | None = None
    updated_at: str | None = None
    heartbeat_age_seconds: int | None = None
    message: str | None = None


@dataclass
class InteractiveSessionInfo:
    has_active_session: bool
    session_id: int | None = None
    username: str | None = None
    is_session_locked: bool | None = None
    message: str | None = None


def get_active_console_session_id() -> int | None:
    try:
        kernel32 = ctypes.windll.kernel32
        session_id = kernel32.WTSGetActiveConsoleSessionId()

        if session_id == 0xFFFFFFFF:
            return None

        return int(session_id)
    except Exception:
        return None


def get_active_session_from_query() -> InteractiveSessionInfo:
    try:
        result = subprocess.run(
            ["query", "session"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
        )

        output = (result.stdout or "").strip()
        if not output:
            return InteractiveSessionInfo(
                has_active_session=False,
                message="Comando query session não retornou saída.",
            )

        lines = [line.rstrip() for line in output.splitlines() if line.strip()]
        if len(lines) <= 1:
            return InteractiveSessionInfo(
                has_active_session=False,
                message="Nenhuma sessão listada pelo query session.",
            )

        for raw_line in lines[1:]:
            normalized = " ".join(raw_line.split())
            parts = normalized.split(" ")

            if len(parts) < 4:
                continue

            username = None
            session_name = None
            session_id = None
            state = None

            if parts[0].startswith(">"):
                parts[0] = parts[0].lstrip(">")

            if len(parts) >= 4:
                username = parts[0]
                session_name = parts[1]
                session_id = parts[2]
                state = parts[3]

            if str(state).strip().lower() == "active":
                try:
                    parsed_session_id = int(str(session_id).strip())
                except Exception:
                    parsed_session_id = None

                return InteractiveSessionInfo(
                    has_active_session=True,
                    session_id=parsed_session_id,
                    username=username,
                    is_session_locked=False,
                    message=f"Sessão ativa detectada: usuário={username} sessão={parsed_session_id} nome={session_name}",
                )

        return InteractiveSessionInfo(
            has_active_session=False,
            message="Nenhuma sessão ACTIVE encontrada no query session.",
        )

    except Exception as exc:
        return InteractiveSessionInfo(
            has_active_session=False,
            message=f"Falha ao consultar sessões: {exc}",
        )


def get_interactive_session_info() -> InteractiveSessionInfo:
    session_info = get_active_session_from_query()

    if session_info.has_active_session:
        return session_info

    console_session_id = get_active_console_session_id()
    if console_session_id is not None:
        return InteractiveSessionInfo(
            has_active_session=True,
            session_id=console_session_id,
            username=None,
            is_session_locked=None,
            message=f"Sessão de console ativa detectada via WTS: {console_session_id}",
        )

    return session_info


def load_interactive_agent_state() -> InteractiveAgentState:
    state_file = Path(INTERACTIVE_AGENT_STATE_FILE)

    if not state_file.exists():
        return InteractiveAgentState(
            is_active=False,
            message=f"Arquivo de estado do agente não encontrado: {state_file}",
        )

    try:
        raw = load_json(state_file)
    except Exception as exc:
        return InteractiveAgentState(
            is_active=False,
            message=f"Falha ao ler arquivo de estado do agente: {exc}",
        )

    if not isinstance(raw, dict):
        return InteractiveAgentState(
            is_active=False,
            message="Arquivo de estado do agente possui conteúdo inválido.",
        )

    try:
        heartbeat_age_seconds = raw.get("heartbeat_age_seconds")
        if heartbeat_age_seconds is not None:
            heartbeat_age_seconds = int(heartbeat_age_seconds)
    except Exception:
        heartbeat_age_seconds = None

    is_active = bool(raw.get("is_active", False))

    if heartbeat_age_seconds is not None and heartbeat_age_seconds > INTERACTIVE_AGENT_HEARTBEAT_TTL_SECONDS:
        is_active = False

    return InteractiveAgentState(
        is_active=is_active,
        pid=_safe_int(raw.get("pid")),
        session_id=_safe_int(raw.get("session_id")),
        username=_safe_str(raw.get("username")),
        updated_at=_safe_str(raw.get("updated_at")),
        heartbeat_age_seconds=heartbeat_age_seconds,
        message=_safe_str(raw.get("message")),
    )


def is_interactive_agent_active() -> tuple[bool, InteractiveAgentState]:
    state = load_interactive_agent_state()

    if not state.is_active:
        return False, state

    session_info = get_interactive_session_info()
    if not session_info.has_active_session:
        state.is_active = False
        state.message = (
            f"Agente aparentemente ativo, mas sem sessão interativa válida. "
            f"Detalhe da sessão: {session_info.message or 'não informado'}"
        )
        return False, state

    if (
        state.session_id is not None
        and session_info.session_id is not None
        and state.session_id != session_info.session_id
    ):
        state.is_active = False
        state.message = (
            f"Agente ativo em sessão diferente da sessão atual. "
            f"agente={state.session_id} atual={session_info.session_id}"
        )
        return False, state

    return True, state


def _safe_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
