from pathlib import Path

APP_NAME = "OrkaFlow Worker"
WORKER_VERSION = "0.2.0"

BASE_DIR = Path(r"C:\OrkaFlow")

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

RUNTIME_EVENT_PATH = "/api/v1/worker/runtime-events"
RUNTIME_EVENT_TYPE_TASK_SKIPPED = "task_skipped"
RUNTIME_EVENT_REASON_WORKER_BUSY = "worker_busy"
RUNTIME_EVENT_REASON_INVALID_EXECUTION_MODE = "invalid_execution_mode"
