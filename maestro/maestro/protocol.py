"""Protocolo entre o robô e o worker: uma linha no stdout por evento.

Formato: ``MAESTRO {json numa linha só}``. O worker lê o stdout linha a linha (o mesmo canal do
``print``), então o relógio de inatividade continua valendo. Linha que o worker não entende vira
log comum: nenhum evento daqui derruba a task.

Alerta de credencial usa a linha antiga ``ORKAFLOW_ALERTA_CREDENCIAL {json}`` e código de saída 3,
que o worker já reconhece.
"""
from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1
MARKER = "MAESTRO"
CREDENTIAL_ALERT_MARKER = "ORKAFLOW_ALERTA_CREDENCIAL"
CREDENTIAL_ALERT_EXIT_CODE = 3

# O worker corta linhas acima de 4000 caracteres: nunca passar disso.
MAX_LINE_CHARS = 3800
MAX_MESSAGE_CHARS = 2000

EVENT_HELLO = "hello"
EVENT_LOG = "log"
EVENT_STEP = "step"
EVENT_SCREENSHOT = "screenshot"
EVENT_HEARTBEAT = "heartbeat"
EVENT_END = "end"
EVENT_ATTACH = "attach"

# Anexo: o worker lê o arquivo na hora e envia ao orquestrador (limite por arquivo).
ATTACH_MAX_BYTES = 5 * 1024 * 1024

LEVELS = ("info", "warning", "error")
CATEGORIES = ("business", "system")
CREDENTIAL_MOTIVOS = ("senha_incorreta", "senha_expirada", "usuario_bloqueado")


def shorten(text: Any, limit: int) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Converte o que não é JSON (datas, Decimal, objetos) em texto, sem estourar em nada."""
    if depth > 6:
        return shorten(repr(value), 200)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v, depth + 1) for v in list(value)[:500]]
    return shorten(str(value), 500)


def encode_line(event: dict[str, Any]) -> str:
    """Monta a linha ``MAESTRO {...}``. Sempre uma linha só e abaixo do limite do worker."""
    payload = {"v": PROTOCOL_VERSION, **_json_safe(event)}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) > MAX_LINE_CHARS:
        # Preferir cortar o campo grande a mandar uma linha que o worker vai truncar no meio do JSON.
        for key in ("data", "traceback", "message", "text", "description"):
            if key in payload:
                payload[key] = shorten(json.dumps(payload[key], ensure_ascii=False, default=str), 600)
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
                if len(text) <= MAX_LINE_CHARS:
                    break
        if len(text) > MAX_LINE_CHARS:
            payload = {"v": PROTOCOL_VERSION, "type": payload.get("type"), "truncated": True}
            text = json.dumps(payload, separators=(",", ":"))
    return f"{MARKER} {text}"


def decode_line(line: str) -> dict[str, Any] | None:
    """Lado do worker: devolve o evento ou None se a linha não é (ou não parece) do Maestro."""
    if not line:
        return None
    stripped = line.strip()
    if not stripped.startswith(MARKER + " "):
        return None
    raw = stripped[len(MARKER) + 1 :].strip()
    if not raw.startswith("{"):
        return None
    try:
        event = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        return None
    return event


def encode_credential_alert(chave: str | None, motivo: str, mensagem: str | None, sistema: str | None) -> str:
    payload = {
        "sistema": sistema,
        "chave": chave,
        "motivo": motivo if motivo in CREDENTIAL_MOTIVOS else "invalida",
        "mensagem": shorten(mensagem, 500),
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("\n", " ")
    return f"{CREDENTIAL_ALERT_MARKER} {text}"
