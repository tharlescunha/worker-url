import os
from pathlib import Path

APP_NAME = "OrkaFlow Worker"
WORKER_VERSION = "0.3.0"

# ORKAFLOW_BASE_DIR existe só para testes; em produção é sempre C:\OrkaFlow.
BASE_DIR = Path(os.environ.get("ORKAFLOW_BASE_DIR") or r"C:\OrkaFlow")

APP_DIR = BASE_DIR / "app"
BOTS_DIR = BASE_DIR / "bots"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
RUNTIME_DIR = BASE_DIR / "runtime"
TMP_DIR = BASE_DIR / "tmp"
TOOLS_DIR = BASE_DIR / "tools"
VENVS_DIR = BASE_DIR / "venvs"

AUTH_FILE = CONFIG_DIR / "auth.json"
RUNNER_FILE = CONFIG_DIR / "runner.json"
WORKER_CONFIG_FILE = CONFIG_DIR / "worker_config.json"
BOTS_REGISTRY_FILE = CONFIG_DIR / "bots_registry.json"

WORKER_BAT_FILE = BASE_DIR / "iniciar_worker.bat"

DEFAULT_HTTP_TIMEOUT = 30

EXECUTION_MODE_BACKGROUND = "background"
EXECUTION_MODE_FOREGROUND = "foreground"

SCREENSHOT_INTERVAL_SECONDS = 10

RUNTIME_EVENT_PATH = "/api/v1/worker/runtime-events/"
RUNTIME_EVENT_TYPE_TASK_SKIPPED = "task_skipped"
RUNTIME_EVENT_REASON_WORKER_BUSY = "worker_busy"
RUNTIME_EVENT_REASON_INVALID_EXECUTION_MODE = "invalid_execution_mode"

RUNTIME_EVENT_TYPE_WORKER_STARTED = "worker_started"
RUNTIME_EVENT_TYPE_SYNC_FAILED = "sync_failed"
RUNTIME_EVENT_TYPE_BOT_INSTALL_FAILED = "bot_install_failed"
RUNTIME_EVENT_TYPE_BOT_AUTO_UPDATE_FAILED = "bot_auto_update_failed"
RUNTIME_EVENT_TYPE_AUTH_FAILED = "auth_failed"
RUNTIME_EVENT_TYPE_FORBIDDEN = "api_forbidden"
RUNTIME_EVENT_TYPE_FOREGROUND_SESSION = "foreground_session_warning"

# Concorrência: até N tasks background (N vem da config do runner, teto abaixo)
# e no máximo 1 foreground por vez. Foreground roda junto com background.
MAX_BACKGROUND_CONCURRENCY_CAP = 5
MAX_FOREGROUND_CONCURRENCY = 1

# Pulso de vida lido pelo supervisor: se parar de ser atualizado, o worker é reiniciado.
PULSE_FILE = RUNTIME_DIR / "worker.pulse"
PULSE_WRITE_INTERVAL_SECONDS = 15
MAIN_LOOP_STALL_LIMIT_SECONDS = 600
SUPERVISOR_STALL_LIMIT_SECONDS = 600
SUPERVISOR_STARTUP_GRACE_SECONDS = 180

HEARTBEAT_INTERVAL_SECONDS = 10

# Robô sem nenhuma saída (stdout/stderr) por este tempo é considerado travado e é
# encerrado pelo worker. A task pode sobrescrever com inactivity_timeout_seconds.
DEFAULT_INACTIVITY_TIMEOUT_SECONDS = 600

# Retenção de arquivos de log/tmp
LOG_RETENTION_DAYS = 5
LOG_MAX_FILE_BYTES = 20 * 1024 * 1024
TMP_RETENTION_DAYS = 2

# Se True, task foreground não inicia com a sessão do Windows bloqueada/desconectada.
# Por padrão só avisa (log + evento), para não bloquear quem usa o truque de tscon.
BLOCK_FOREGROUND_WHEN_SESSION_LOCKED = False
