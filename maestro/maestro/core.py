"""A classe Maestro: o que o robô usa para ler a task e falar com o orquestrador.

Em produção (padrão) lê o arquivo que o worker escreveu (``ORKAFLOW_TASK_FILE``) e escreve linhas
``MAESTRO {json}`` no stdout. Em ``producao=False`` lê ``task.json`` ao lado do robô, imprime
logs no console e grava ``resultado.json``: nenhum acesso à rede, nada do orquestrador.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import protocol
from .capture import capture_screen

__version__ = "1.0.0"

ENV_TASK_FILE = "ORKAFLOW_TASK_FILE"
ENV_RESULT_FILE = "ORKAFLOW_RESULT_FILE"
ENV_TASK_ID = "ORKAFLOW_TASK_ID"

LOCAL_TASK_FILE = "task.json"
LOCAL_RESULT_FILE = "resultado.json"
LOCAL_SCREENSHOT_DIR = "screenshots"

EXIT_SUCCESS = 0
EXIT_ERROR = 1


class MaestroError(Exception):
    """Erro de uso do Maestro (parâmetro/credencial inexistente, etc.)."""


class CredencialInvalida(Exception):
    """Levante para avisar que a credencial não serve mais (senha errada/vencida, usuário bloqueado).

    O orquestrador marca a credencial, pausa os agendamentos que a usam e bloqueia tasks novas.
    ``motivo``: senha_incorreta | senha_expirada | usuario_bloqueado.
    """

    def __init__(self, chave: str | None, motivo: str = "invalida", mensagem: str | None = None, sistema: str | None = None):
        super().__init__(mensagem or f"Credencial inválida: {chave or '?'} ({motivo})")
        self.chave = chave
        self.motivo = motivo
        self.mensagem = mensagem
        self.sistema = sistema


@dataclass
class TaskInfo:
    id: int | None = None
    automation_id: int | None = None
    bot_id: int | None = None
    correlation_id: str | None = None
    execution_mode: str | None = None
    priority: int | None = None
    queue_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Maestro:
    def __init__(
        self,
        producao: bool = True,
        screenshot_on_error: bool = False,
        task_file: str | os.PathLike[str] | None = None,
        result_file: str | os.PathLike[str] | None = None,
        stream=None,
    ) -> None:
        self.producao = bool(producao)
        self.screenshot_on_error = bool(screenshot_on_error)
        self._stream = stream  # None = sys.stdout no momento do uso (facilita testes)
        self._explicit_task_file = Path(task_file) if task_file else None
        self._explicit_result_file = Path(result_file) if result_file else None

        self._task_file: Path | None = None
        self._result_file: Path | None = None
        self._loaded = False
        self._params: dict[str, Any] = {}
        self._credentials: dict[str, Any] = {}
        self._env_local: dict[str, str] = {}
        self._task = TaskInfo()

        self._next_bots: list[dict[str, Any]] = []
        self._result: Any = None
        self._finished = False
        self._screenshot_seq = 0
        self._hello_sent = False

    # ------------------------------------------------------------------ entrada
    @property
    def task(self) -> TaskInfo:
        self._ensure_loaded()
        return self._task

    @property
    def params(self) -> dict[str, Any]:
        """parameters_json (sem dados_acesso) mesclado com runtime_parameters_json."""
        self._ensure_loaded()
        return dict(self._params)

    def param(self, name: str, default: Any = None) -> Any:
        self._ensure_loaded()
        return self._params.get(name, default)

    def require(self, name: str) -> Any:
        """Como ``param``, mas falha com mensagem clara se o parâmetro não veio."""
        self._ensure_loaded()
        if name not in self._params:
            raise MaestroError(f"Parâmetro obrigatório '{name}' não foi informado na task.")
        return self._params[name]

    def credential(self, key: str, default: Any = ...) -> Any:
        """Valor já resolvido de dados_acesso (ex.: 'sap_senha'). Nunca logue o retorno."""
        self._ensure_loaded()
        if key in self._credentials and self._credentials[key] is not None:
            return self._credentials[key]
        if default is not ...:
            return default
        available = ", ".join(sorted(self._credentials)) or "nenhuma"
        raise MaestroError(f"Credencial '{key}' não está em dados_acesso desta task (disponíveis: {available}).")

    @property
    def credentials(self) -> dict[str, Any]:
        self._ensure_loaded()
        return dict(self._credentials)

    def env(self, name: str, default: str | None = None) -> str | None:
        """Variável de ambiente (as do orquestrador o worker injeta no processo)."""
        self._ensure_loaded()
        value = os.environ.get(name)
        if value is None:
            value = self._env_local.get(name)
        return default if value is None else value

    # ------------------------------------------------------------------- saída
    def info(self, message: str, **data: Any) -> None:
        self._log("info", message, data)

    def warning(self, message: str, **data: Any) -> None:
        self._log("warning", message, data)

    def error(self, message: str, **data: Any) -> None:
        """Registra um erro no log sem encerrar o robô (para encerrar use ``fail``)."""
        self._log("error", message, data)

    def step(self, current: int, total: int | None = None, text: str | None = None) -> None:
        self._emit({"type": protocol.EVENT_STEP, "current": int(current), "total": None if total is None else int(total), "text": protocol.shorten(text, 200)})

    def heartbeat(self) -> None:
        """Para laços longos sem saída: evita o worker achar que o robô travou."""
        self._emit({"type": protocol.EVENT_HEARTBEAT, "at": datetime.now().isoformat(timespec="seconds")})

    @contextmanager
    def timer(self, label: str) -> Iterator[None]:
        started = time.monotonic()
        self.info(f"{label}: início")
        try:
            yield
        finally:
            self.info(f"{label}: {time.monotonic() - started:.1f}s")

    def screenshot(self, description: str | None = None) -> Path | None:
        """Tira um print da tela agora e anexa à task. Devolve o caminho do PNG ou None se falhou."""
        self._screenshot_seq += 1
        target = self._screenshot_path(self._screenshot_seq)
        ok = capture_screen(target)
        if not ok:
            self.warning("Não foi possível tirar o print da tela.")
            return None
        self._emit({"type": protocol.EVENT_SCREENSHOT, "path": str(target), "description": protocol.shorten(description, 200)})
        return target

    def next_bot(self, automation: str | int, parametros: dict[str, Any] | None = None) -> None:
        """Ao terminar com sucesso, coloca outra automação na fila (nome ou id)."""
        if parametros is not None and not isinstance(parametros, dict):
            raise MaestroError("parametros de next_bot precisa ser um dicionário.")
        item: dict[str, Any] = {"parameters": parametros}
        if isinstance(automation, int):
            item["automation_id"] = automation
        else:
            item["automation"] = str(automation).strip()
        self._next_bots.append(item)

    # ------------------------------------------------------------ encerramento
    def success(self, message: str | None = None, result: Any = None) -> None:
        """Grava o resultado, avisa o worker e encerra o processo com código 0."""
        self._result = result
        self._write_result()
        self._end("success", message or "Task finalizada com sucesso.")
        raise SystemExit(EXIT_SUCCESS)

    def fail(self, message: str, category: str = "system", retryable: bool | None = None, exit_code: int = EXIT_ERROR) -> None:
        """Encerra com erro. category: business (regra de negócio) ou system (ambiente)."""
        if category not in protocol.CATEGORIES:
            category = "system"
        if retryable is None:
            retryable = category == "system"
        self._end("error", message, category=category, retryable=bool(retryable))
        raise SystemExit(exit_code if exit_code != 0 else EXIT_ERROR)

    def credential_invalid(self, chave: str | None, motivo: str = "invalida", mensagem: str | None = None, sistema: str | None = None) -> None:
        raise CredencialInvalida(chave, motivo=motivo, mensagem=mensagem, sistema=sistema)

    # --------------------------------------------------------- bloco `with`
    def __enter__(self) -> "Maestro":
        self._ensure_loaded()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            if not self._finished:
                self._write_result()
                self._end("success", "Task finalizada com sucesso.")
            return False
        if issubclass(exc_type, SystemExit):
            # success()/fail() já avisaram; saída manual do robô também passa reto.
            if not self._finished:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
                if code == 0:
                    self._write_result()
                    self._end("success", "Task finalizada com sucesso.")
                else:
                    self._end("error", f"Robô encerrou com código de saída {code}.", category="system", retryable=True)
            return False
        if issubclass(exc_type, KeyboardInterrupt):
            return False
        if issubclass(exc_type, CredencialInvalida):
            self._emit_raw(protocol.encode_credential_alert(exc.chave, exc.motivo, exc.mensagem or str(exc), exc.sistema))
            self._end("error", f"Credencial inválida: {exc.mensagem or exc}", category="business", retryable=False)
            raise SystemExit(protocol.CREDENTIAL_ALERT_EXIT_CODE)
        # Qualquer outra exceção: erro com traceback, print se pedido, saída 1.
        trace = "".join(traceback.format_exception(exc_type, exc, tb))
        self._log("error", f"{exc_type.__name__}: {exc}", {"traceback": protocol.shorten(trace, 2500)})
        if self.screenshot_on_error:
            self.screenshot("Tela no momento do erro")
        self._end("error", f"{exc_type.__name__}: {exc}", category="system", retryable=True)
        if not self.producao:
            # Na máquina do desenvolvedor o traceback completo no console ajuda mais.
            sys.stderr.write(trace)
        raise SystemExit(EXIT_ERROR)

    # ------------------------------------------------------------- internos
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._resolve_task_file()
        self._task_file = path
        data: dict[str, Any] = {}
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception as exc:  # noqa: BLE001
                self.warning(f"Maestro: não consegui ler {path.name}: {exc}")
        elif self.producao:
            self.warning("Maestro: arquivo da task não encontrado; rodando sem parâmetros (você está fora do orquestrador?).")

        self._task = TaskInfo(
            id=_as_int(data.get("task_id") or data.get("id")),
            automation_id=_as_int(data.get("automation_id")),
            bot_id=_as_int(data.get("bot_id")),
            correlation_id=data.get("correlation_id"),
            execution_mode=data.get("execution_mode"),
            priority=_as_int(data.get("priority")),
            queue_name=data.get("queue_name"),
            raw=data,
        )
        parameters_json = _as_dict(data.get("parameters_json"))
        if not parameters_json:
            parameters_json = _as_dict(data.get("credentials_json"))
        runtime = _as_dict(data.get("runtime_parameters_json"))
        if not runtime:
            runtime = _as_dict(data.get("runtime_parameters"))

        credentials = _as_dict(parameters_json.get("dados_acesso"))
        # Formato achatado ({"chave": "valor"}) é o que o worker entrega; aceita também o
        # formato de cadastro ({"credential_id": N, "itens": {...}}) para o task.json local.
        if "itens" in credentials and isinstance(credentials.get("itens"), dict):
            credentials = _as_dict(credentials["itens"])
        self._credentials = {str(k): v for k, v in credentials.items()}

        merged = {k: v for k, v in parameters_json.items() if k != "dados_acesso"}
        merged.update(runtime)
        self._params = merged
        self._env_local = {str(k): str(v) for k, v in _as_dict(data.get("env")).items()} if not self.producao else {}

        self._result_file = self._resolve_result_file()
        self._send_hello()

    def _resolve_task_file(self) -> Path | None:
        if self._explicit_task_file is not None:
            return self._explicit_task_file
        if self.producao:
            value = os.environ.get(ENV_TASK_FILE)
            if value:
                return Path(value)
            # Produção sem orquestrador (ex.: esqueceram producao=False): tenta o local, sem quebrar.
        return self._local_base_dir() / LOCAL_TASK_FILE

    def _resolve_result_file(self) -> Path | None:
        if self._explicit_result_file is not None:
            return self._explicit_result_file
        if self.producao:
            value = os.environ.get(ENV_RESULT_FILE)
            if value:
                return Path(value)
        return self._local_base_dir() / LOCAL_RESULT_FILE

    def _screenshot_path(self, seq: int) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_part = self._task.id if self._task.id is not None else "local"
        if self.producao and self._task_file is not None and os.environ.get(ENV_TASK_FILE):
            return self._task_file.parent / f"maestro_shot_{task_part}_{seq}_{stamp}.png"
        return self._local_base_dir() / LOCAL_SCREENSHOT_DIR / f"shot_{seq}_{stamp}.png"

    @staticmethod
    def _local_base_dir() -> Path:
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            return Path(main_file).resolve().parent
        return Path.cwd()

    def _write_result(self) -> None:
        if self._result_file is None:
            return
        payload: dict[str, Any]
        if isinstance(self._result, dict):
            payload = dict(self._result)
        elif self._result is None:
            payload = {}
        else:
            payload = {"result": self._result}
        if self._next_bots:
            workflow = _as_dict(payload.get("orkaflow"))
            workflow["next_automations"] = list(self._next_bots)
            payload["orkaflow"] = workflow
        if not payload:
            return
        try:
            self._result_file.parent.mkdir(parents=True, exist_ok=True)
            self._result_file.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.warning(f"Maestro: não consegui gravar o resultado: {exc}")

    def _end(self, status: str, message: str, category: str | None = None, retryable: bool | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        event: dict[str, Any] = {"type": protocol.EVENT_END, "status": status, "message": protocol.shorten(message, protocol.MAX_MESSAGE_CHARS)}
        if status == "error":
            event["category"] = category or "system"
            event["retryable"] = bool(retryable)
        if self._next_bots:
            event["next_bots"] = [b.get("automation") or b.get("automation_id") for b in self._next_bots]
        self._emit(event)

    def _log(self, level: str, message: str, data: dict[str, Any]) -> None:
        self._ensure_loaded()
        event: dict[str, Any] = {"type": protocol.EVENT_LOG, "level": level, "message": protocol.shorten(message, protocol.MAX_MESSAGE_CHARS)}
        if data:
            event["data"] = data
        self._emit(event)

    def _send_hello(self) -> None:
        if self._hello_sent:
            return
        self._hello_sent = True
        self._emit({"type": protocol.EVENT_HELLO, "version": __version__, "python": sys.version.split()[0]})

    def _emit(self, event: dict[str, Any]) -> None:
        if self.producao:
            self._emit_raw(protocol.encode_line(event))
        else:
            self._emit_raw(_format_local(event))

    def _emit_raw(self, line: str) -> None:
        stream = self._stream or sys.stdout
        try:
            stream.write(line + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001
            # stdout fechado/redirecionado: o robô não pode morrer por causa de log.
            pass


def _as_int(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _format_local(event: dict[str, Any]) -> str:
    """Modo local: linha legível no console em vez do JSON."""
    kind = event.get("type")
    if kind == protocol.EVENT_HELLO:
        return f"[maestro {event.get('version')}] modo local (producao=False)"
    if kind == protocol.EVENT_LOG:
        level = str(event.get("level", "info")).upper()
        extra = event.get("data")
        suffix = f" {json.dumps(extra, ensure_ascii=False, default=str)}" if extra else ""
        return f"[{level}] {event.get('message')}{suffix}"
    if kind == protocol.EVENT_STEP:
        total = f"/{event['total']}" if event.get("total") else ""
        return f"[STEP {event.get('current')}{total}] {event.get('text') or ''}".rstrip()
    if kind == protocol.EVENT_SCREENSHOT:
        return f"[SCREENSHOT] {event.get('path')} {event.get('description') or ''}".rstrip()
    if kind == protocol.EVENT_HEARTBEAT:
        return "[HEARTBEAT]"
    if kind == protocol.EVENT_END:
        status = str(event.get("status", "")).upper()
        tail = ""
        if event.get("next_bots"):
            tail = f" | próximos robôs: {event['next_bots']}"
        return f"[END {status}] {event.get('message')}{tail}"
    return json.dumps(event, ensure_ascii=False, default=str)
