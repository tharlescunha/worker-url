from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


from app.core.constants import (
    EXECUTION_MODE_FOREGROUND,
    INTERACTIVE_AGENT_HEARTBEAT_INTERVAL_SECONDS,
    INTERACTIVE_AGENT_LOG_FILE,
    INTERACTIVE_AGENT_QUEUE_DIR,
    INTERACTIVE_AGENT_RESULTS_DIR,
    INTERACTIVE_AGENT_STATE_FILE,
)


def setup_interactive_agent_logger() -> logging.Logger:
    logger = logging.getLogger("orkaflow_interactive_agent")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    INTERACTIVE_AGENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(INTERACTIVE_AGENT_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


class InteractiveAgent:
    def __init__(self) -> None:
        self.logger = setup_interactive_agent_logger()
        self.pid = os.getpid()

        INTERACTIVE_AGENT_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_AGENT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        INTERACTIVE_AGENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        self.logger.info("Agente interativo iniciado | pid=%s", self.pid)

        while True:
            try:
                self._write_heartbeat_state()

                request_file = self._get_next_request_file()
                if request_file is not None:
                    self._process_request_file(request_file)

            except Exception as exc:
                self.logger.exception(
                    "Erro no loop principal do agente interativo | pid=%s erro=%s",
                    self.pid,
                    exc,
                )

            time.sleep(INTERACTIVE_AGENT_HEARTBEAT_INTERVAL_SECONDS)

    def _get_next_request_file(self) -> Path | None:
        files = sorted(
            [
                file_path
                for file_path in INTERACTIVE_AGENT_QUEUE_DIR.glob("*.json")
                if file_path.is_file()
            ],
            key=lambda path: path.stat().st_mtime,
        )

        if not files:
            return None

        for file_path in files:
            if self._is_file_stable(file_path):
                return file_path

        return None

    def _process_request_file(self, request_file: Path) -> None:
        self.logger.info("Processando solicitação foreground | file=%s", request_file)

        try:
            request_payload = self._load_json_file(request_file)
        except Exception as exc:
            self.logger.exception(
                "Falha ao ler solicitação foreground | file=%s erro=%s",
                request_file,
                exc,
            )
            self._safe_delete_file(request_file)
            return

        execution_request_id = str(request_payload.get("execution_request_id") or "").strip()
        if not execution_request_id:
            self.logger.error(
                "Solicitação foreground sem execution_request_id | file=%s",
                request_file,
            )
            self._safe_delete_file(request_file)
            return

        result_file = INTERACTIVE_AGENT_RESULTS_DIR / f"{execution_request_id}.json"

        try:
            result_payload = self._execute_request(request_payload)
        except Exception as exc:
            self.logger.exception(
                "Erro ao executar solicitação foreground | request_id=%s erro=%s",
                execution_request_id,
                exc,
            )
            result_payload = {
                "success": False,
                "status": "error",
                "final_message": f"Erro no agente interativo: {exc}",
                "execution_request_id": execution_request_id,
                "stdout_text": None,
                "stderr_text": str(exc),
                "exit_code": None,
                "finished_at": datetime.now(UTC).isoformat(),
            }

        self._write_json_atomic(result_file, result_payload)
        self._safe_delete_file(request_file)

        self.logger.info(
            "Solicitação foreground concluída | request_id=%s status=%s success=%s",
            execution_request_id,
            result_payload.get("status"),
            result_payload.get("success"),
        )

    def _execute_request(self, request_payload: dict) -> dict:
        execution_request_id = str(request_payload.get("execution_request_id") or "").strip()
        task_id = request_payload.get("task_id")
        timeout_seconds = int(request_payload.get("timeout_seconds") or 300)

        python_executable = Path(str(request_payload.get("python_executable") or "").strip())
        entrypoint = Path(str(request_payload.get("entrypoint") or "").strip())
        working_directory = Path(str(request_payload.get("working_directory") or "").strip())
        payload_file = Path(str(request_payload.get("payload_file") or "").strip())

        if not python_executable.exists():
            raise RuntimeError(f"Python do bot não encontrado: {python_executable}")

        if not entrypoint.exists():
            raise RuntimeError(f"Entrypoint do bot não encontrado: {entrypoint}")

        if not working_directory.exists():
            raise RuntimeError(f"Diretório de trabalho do bot não encontrado: {working_directory}")

        if not payload_file.exists():
            raise RuntimeError(f"Arquivo de payload não encontrado: {payload_file}")

        env = os.environ.copy()
        extra_env = request_payload.get("environment") or {}
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                env[str(key)] = "" if value is None else str(value)

        env["ORKAFLOW_EXECUTION_MODE"] = EXECUTION_MODE_FOREGROUND
        env["ORKAFLOW_INTERACTIVE_AGENT_PID"] = str(self.pid)

        command = [
            str(python_executable),
            str(entrypoint),
            str(payload_file),
        ]

        started_at = datetime.now(UTC)

        self.logger.info(
            "Iniciando bot foreground | request_id=%s task_id=%s command=%s",
            execution_request_id,
            task_id,
            command,
        )

        process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            shell=False,
        )

        try:
            stdout_text, stderr_text = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_process_tree(process.pid)

            finished_at = datetime.now(UTC)
            return {
                "success": False,
                "status": "timeout",
                "final_message": "Task foreground excedeu o timeout no agente interativo.",
                "execution_request_id": execution_request_id,
                "stdout_text": None,
                "stderr_text": "TimeoutExpired",
                "exit_code": None,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
            }

        finished_at = datetime.now(UTC)
        success = process.returncode == 0

        return {
            "success": success,
            "status": "finished" if success else "error",
            "final_message": (
                "Task foreground finalizada com sucesso."
                if success
                else f"Bot foreground finalizou com código de saída {process.returncode}."
            ),
            "execution_request_id": execution_request_id,
            "stdout_text": stdout_text,
            "stderr_text": stderr_text,
            "exit_code": process.returncode,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        }

    def _write_heartbeat_state(self) -> None:
        state_payload = {
            "is_active": True,
            "pid": self.pid,
            "session_id": self._get_session_id(),
            "username": self._get_username(),
            "updated_at": datetime.now(UTC).isoformat(),
            "heartbeat_age_seconds": 0,
            "message": "Agente interativo ativo.",
        }

        self._write_json_atomic(INTERACTIVE_AGENT_STATE_FILE, state_payload)

    def _write_json_atomic(self, file_path: Path, payload: dict) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)

        temp_file = file_path.with_suffix(f"{file_path.suffix}.tmp")
        temp_file.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp_file, file_path)

    def _load_json_file(self, file_path: Path) -> dict:
        raw = file_path.read_text(encoding="utf-8")
        data = json.loads(raw)

        if not isinstance(data, dict):
            raise ValueError(f"Conteúdo JSON inválido em {file_path}")

        return data

    def _is_file_stable(self, file_path: Path, wait_seconds: float = 0.2) -> bool:
        try:
            first_size = file_path.stat().st_size
            time.sleep(wait_seconds)
            second_size = file_path.stat().st_size
            return first_size == second_size
        except Exception:
            return False

    def _safe_delete_file(self, file_path: Path) -> None:
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as exc:
            self.logger.warning(
                "Não foi possível remover arquivo do agente interativo | file=%s erro=%s",
                file_path,
                exc,
            )

    def _get_username(self) -> str | None:
        try:
            return os.getlogin()
        except Exception:
            return None

    def _get_session_id(self) -> int | None:
        try:
            session_id = ctypes.c_ulong()
            kernel32 = ctypes.windll.kernel32

            result = kernel32.ProcessIdToSessionId(
                ctypes.c_uint(self.pid),
                ctypes.byref(session_id),
            )

            if result == 0:
                return None

            return int(session_id.value)
        except Exception:
            return None

    def _kill_process_tree(self, pid: int) -> None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )


def main() -> None:
    agent = InteractiveAgent()
    agent.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
        