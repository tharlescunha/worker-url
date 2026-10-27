"""Linhas ``MAESTRO {json}`` que o robô (biblioteca maestro) escreve no stdout.

Regra: nada aqui pode derrubar a task. Linha que não parsear, tipo desconhecido ou qualquer
exceção no tratamento devolve None, e o leitor de stdout segue o caminho de sempre (texto comum).
O worker não importa o pacote maestro: o protocolo é pequeno e fica duplicado aqui de propósito,
para o worker não depender do venv de nenhum bot.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

MAESTRO_MARKER = "MAESTRO"
SUPPORTED_PROTOCOL = 1

EVENT_HELLO = "hello"
EVENT_LOG = "log"
EVENT_STEP = "step"
EVENT_SCREENSHOT = "screenshot"
EVENT_HEARTBEAT = "heartbeat"
EVENT_END = "end"
EVENT_ATTACH = "attach"
KNOWN_EVENTS = {EVENT_HELLO, EVENT_LOG, EVENT_STEP, EVENT_SCREENSHOT, EVENT_HEARTBEAT, EVENT_END, EVENT_ATTACH}

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"
LEVELS = {LEVEL_INFO, LEVEL_WARNING, LEVEL_ERROR}

MAX_SCREENSHOTS_KEPT = 5
MAX_ATTACHMENTS_PER_TASK = 10
ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
# error_type enviado ao backend conforme a categoria do fail() do robô.
ERROR_TYPE_BY_CATEGORY = {"business": "bot_business_error", "system": "bot_system_error"}


@dataclass
class MaestroEnd:
    status: str
    message: str
    category: str | None = None
    retryable: bool = False


@dataclass
class MaestroStep:
    current: int
    total: int | None
    text: str | None


@dataclass
class MaestroState:
    """O que o worker guarda da task enquanto o robô roda (uma instância por task)."""

    version: str | None = None
    end: MaestroEnd | None = None
    last_step: MaestroStep | None = None
    screenshots: list[dict[str, Any]] = field(default_factory=list)
    # Anexos pedidos pelo robô (attach): {path, name, description, size}; o executor envia.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    attachments_sent: int = 0
    events_seen: int = 0
    invalid_lines: int = 0

    @property
    def used(self) -> bool:
        return self.events_seen > 0


def decode_line(line: str) -> dict[str, Any] | None:
    """Devolve o evento, ou None se a linha não é (ou não parece) do Maestro."""
    if not line:
        return None
    stripped = line.strip()
    if not stripped.startswith(MAESTRO_MARKER + " "):
        return None
    raw = stripped[len(MAESTRO_MARKER) + 1 :].strip()
    if not raw.startswith("{"):
        return None
    try:
        event = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(event, dict) or event.get("type") not in KNOWN_EVENTS:
        return None
    return event


def handle_line(state: MaestroState | None, line: str) -> tuple[str, str, bool] | None:
    """Interpreta uma linha de stdout.

    Devolve ``(nivel, texto_para_o_log, enviar)`` quando a linha é do Maestro, ou None para o
    leitor tratar como texto comum. Nunca levanta exceção.
    """
    if state is None:
        return None
    try:
        event = decode_line(line)
        if event is None:
            if line.lstrip().startswith(MAESTRO_MARKER + " "):
                state.invalid_lines += 1
            return None
        state.events_seen += 1
        return _apply(state, event)
    except Exception:  # noqa: BLE001
        return None


def _apply(state: MaestroState, event: dict[str, Any]) -> tuple[str, str, bool]:
    kind = event["type"]

    if kind == EVENT_HELLO:
        state.version = _text(event.get("version"))
        return LEVEL_INFO, f"Maestro {state.version or '?'} iniciado no robô.", True

    if kind == EVENT_LOG:
        level = event.get("level") if event.get("level") in LEVELS else LEVEL_INFO
        return level, _format_log(event), True

    if kind == EVENT_STEP:
        current = _int(event.get("current")) or 0
        total = _int(event.get("total"))
        text = _text(event.get("text"))
        state.last_step = MaestroStep(current=current, total=total, text=text)
        label = f"Etapa {current}" + (f" de {total}" if total else "") + (f": {text}" if text else "")
        return LEVEL_INFO, label, True

    if kind == EVENT_SCREENSHOT:
        path = _text(event.get("path"))
        if path:
            state.screenshots.append({"path": path, "description": _text(event.get("description"))})
            del state.screenshots[:-MAX_SCREENSHOTS_KEPT]
        description = _text(event.get("description"))
        return LEVEL_INFO, "Print da tela registrado pelo robô" + (f": {description}" if description else "."), True

    if kind == EVENT_ATTACH:
        path = _text(event.get("path"))
        name = _text(event.get("name")) or (os.path.basename(path) if path else None)
        if not path:
            return LEVEL_WARNING, "Anexo ignorado: o robô não informou o caminho do arquivo.", True
        if len(state.attachments) >= MAX_ATTACHMENTS_PER_TASK:
            return LEVEL_WARNING, f"Anexo ignorado ({name}): limite de {MAX_ATTACHMENTS_PER_TASK} arquivos por task.", True
        state.attachments.append(
            {"path": path, "name": name, "description": _text(event.get("description")), "size": _int(event.get("size"))}
        )
        description = _text(event.get("description"))
        return LEVEL_INFO, f"Arquivo anexado pelo robô: {name}" + (f" ({description})" if description else ""), True

    if kind == EVENT_HEARTBEAT:
        # Só serve para o relógio de inatividade; não polui o log da task.
        return LEVEL_INFO, "", False

    # EVENT_END
    status = "error" if event.get("status") == "error" else "success"
    message = _text(event.get("message")) or ("Task finalizada com sucesso." if status == "success" else "Robô encerrou com erro.")
    category = _text(event.get("category"))
    state.end = MaestroEnd(status=status, message=message, category=category, retryable=bool(event.get("retryable")))
    next_automations = event.get("next_automations")
    tail = (
        f" Próximas automações: {', '.join(str(b) for b in next_automations)}."
        if isinstance(next_automations, list) and next_automations else ""
    )
    level = LEVEL_ERROR if status == "error" else LEVEL_INFO
    return level, f"Robô encerrou ({status}): {message}{tail}", True


def _format_log(event: dict[str, Any]) -> str:
    message = _text(event.get("message")) or ""
    data = event.get("data")
    if isinstance(data, dict) and data:
        parts = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, default=str)
            parts.append(f"{key}={value}")
        return f"{message} | " + " ".join(parts) if message else " ".join(parts)
    return message


def error_type_for(end: MaestroEnd | None, default: str) -> str:
    if end is None or end.status != "error":
        return default
    return ERROR_TYPE_BY_CATEGORY.get(end.category or "", default)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
