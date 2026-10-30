"""Atualização pedida pelo painel (Configurações > Works).

O backend responde ao heartbeat com ``update_requested``. O worker então para de pegar tasks novas, espera as
em andamento terminarem e sai com código 0. O supervisor devolve o controle ao ``iniciar_worker.bat``, que faz
``git fetch`` + ``reset --hard`` + ``clean`` e sobe tudo de novo, já no código novo.

Cuidados para nunca derrubar o worker por causa disso:
- tudo aqui engole erro: pior caso, o pedido é ignorado e o worker segue como sempre;
- só reinicia se estiver sob o supervisor (sem ele ninguém reabriria o processo);
- cada pedido é atendido uma vez só: o código do pedido fica gravado em disco antes de sair, então
  relógio errado ou resposta repetida do backend nunca causam reinício em laço.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

import psutil

from app.core.constants import RUNTIME_DIR

logger = logging.getLogger("orkaflow_worker")

HANDLED_FILE = RUNTIME_DIR / "update_handled.txt"
REPO_DIR = Path(__file__).resolve().parents[2]

_lock = threading.Lock()
_pending_id: str | None = None
_commit_cache: str | None = None


def current_commit() -> str | None:
    """Commit (7 caracteres) do código em execução; None se não houver Git ali."""
    global _commit_cache
    if _commit_cache:
        return _commit_cache
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = (result.stdout or "").strip().lower()
        if result.returncode == 0 and 7 <= len(value) <= 40:
            _commit_cache = value
    except Exception:
        pass
    return _commit_cache


def handled_id() -> str | None:
    """Código do último pedido atendido (confirmado ao backend no heartbeat)."""
    try:
        value = HANDLED_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def under_supervisor() -> bool:
    try:
        parent = psutil.Process().parent()
        return bool(parent and "app.runtime.supervisor" in " ".join(parent.cmdline()))
    except Exception:
        return False


def on_heartbeat_response(response: dict | None) -> None:
    """Chamado a cada resposta do heartbeat; guarda o pedido pendente (ou o limpa)."""
    global _pending_id
    try:
        request_id = (response or {}).get("update_request_id")
        wanted = bool((response or {}).get("update_requested")) and bool(request_id)
        with _lock:
            if not wanted or request_id == handled_id():
                _pending_id = None
                return
            if not under_supervisor():
                if _pending_id != request_id:
                    logger.warning("Atualização pedida pelo painel ignorada: o worker não está sob o supervisor.")
                _pending_id = None
                return
            if _pending_id != request_id:
                logger.warning(
                    "Atualização pedida pelo painel. Sem pegar tasks novas; reinicia quando as atuais terminarem."
                )
            _pending_id = request_id
    except Exception:
        pass


def draining() -> bool:
    """True enquanto há pedido de atualização: não inicia tasks novas."""
    return _pending_id is not None


def should_restart(active_tasks: int) -> bool:
    return _pending_id is not None and active_tasks == 0


def mark_handled() -> None:
    """Grava o código do pedido antes de sair; falhar aqui cancela o reinício (melhor que um laço)."""
    global _pending_id
    request_id = _pending_id
    if not request_id:
        return
    HANDLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    HANDLED_FILE.write_text(request_id, encoding="utf-8")
    with _lock:
        _pending_id = None


def restart_now_if_idle(active_tasks: int) -> bool:
    """True se o worker deve sair agora para o launcher atualizar. Nunca levanta."""
    try:
        if not should_restart(active_tasks):
            return False
        mark_handled()
        return True
    except Exception as exc:
        logger.warning("Não foi possível registrar o pedido de atualização; reinício cancelado: %s", exc)
        return False
