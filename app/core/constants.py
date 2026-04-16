# app\core\constants.py

"""
Constantes centrais do worker.
"""

from pathlib import Path

APP_NAME = "OrkaFlow Worker"
WORKER_VERSION = "0.1.0"

BASE_DIR = Path(r"C:\OrkaFlow")

APP_DIR = BASE_DIR / "app"
BOTS_DIR = BASE_DIR / "bots"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
SERVICES_DIR = BASE_DIR / "services"
TMP_DIR = BASE_DIR / "tmp"
TOOLS_DIR = BASE_DIR / "tools"
VENVS_DIR = BASE_DIR / "venvs"
RUNTIME_DIR = BASE_DIR / "runtime"

SERVICE_NAME = "OrkaFlowWorker"
SERVICE_DISPLAY_NAME = "OrkaFlow Worker"
SERVICE_DESCRIPTION = "Serviço do worker do OrkaFlow"

AUTH_FILE = CONFIG_DIR / "auth.json"
RUNNER_FILE = CONFIG_DIR / "runner.json"
WORKER_CONFIG_FILE = CONFIG_DIR / "worker_config.json"
BOTS_REGISTRY_FILE = CONFIG_DIR / "bots_registry.json"

SERVICE_CONFIG_FILE = SERVICES_DIR / "worker_service_config.json"
INSTALL_SERVICE_BAT = SERVICES_DIR / "install_worker_service.bat"
START_SERVICE_BAT = SERVICES_DIR / "start_worker.bat"
STOP_SERVICE_BAT = SERVICES_DIR / "stop_worker.bat"
RESTART_SERVICE_BAT = SERVICES_DIR / "restart_worker.bat"
DIAGNOSTIC_SERVICE_BAT = SERVICES_DIR / "diagnostic_worker.bat"

# legado - interactive agent
INTERACTIVE_AGENT_STATE_FILE = TMP_DIR / "interactive_agent_state.json"
INTERACTIVE_AGENT_QUEUE_DIR = TMP_DIR / "interactive_agent_queue"
INTERACTIVE_AGENT_RESULTS_DIR = TMP_DIR / "interactive_agent_results"
INTERACTIVE_AGENT_LOG_FILE = LOGS_DIR / "interactive_agent.log"
INTERACTIVE_AGENT_STDOUT_LOG = LOGS_DIR / "interactive_agent_stdout.log"
INTERACTIVE_AGENT_STDERR_LOG = LOGS_DIR / "interactive_agent_stderr.log"

INTERACTIVE_AGENT_SCRIPT = RUNTIME_DIR / "interactive_agent_launcher.py"
INTERACTIVE_AGENT_VBS = RUNTIME_DIR / "interactive_agent_launcher.vbs"
INTERACTIVE_AGENT_TASK_XML = SERVICES_DIR / "interactive_agent_task.xml"
INSTALL_INTERACTIVE_AGENT_BAT = SERVICES_DIR / "install_interactive_agent_task.bat"
REMOVE_INTERACTIVE_AGENT_BAT = SERVICES_DIR / "remove_interactive_agent_task.bat"
RUN_INTERACTIVE_AGENT_BAT = SERVICES_DIR / "run_interactive_agent.bat"
DIAGNOSTIC_INTERACTIVE_AGENT_BAT = SERVICES_DIR / "diagnostic_interactive_agent.bat"

INTERACTIVE_AGENT_TASK_NAME = "OrkaFlowInteractiveAgent"
INTERACTIVE_AGENT_DISPLAY_NAME = "OrkaFlow Interactive Agent"

# novo - interactive worker
INTERACTIVE_WORKER_LOG_FILE = LOGS_DIR / "interactive_worker.log"
INTERACTIVE_WORKER_STDOUT_LOG = LOGS_DIR / "interactive_worker_stdout.log"
INTERACTIVE_WORKER_STDERR_LOG = LOGS_DIR / "interactive_worker_stderr.log"

INTERACTIVE_WORKER_SCRIPT = RUNTIME_DIR / "interactive_worker_launcher.py"
INTERACTIVE_WORKER_VBS = RUNTIME_DIR / "interactive_worker_launcher.vbs"
INTERACTIVE_WORKER_TASK_XML = SERVICES_DIR / "interactive_worker_task.xml"
INSTALL_INTERACTIVE_WORKER_BAT = SERVICES_DIR / "install_interactive_worker_task.bat"
REMOVE_INTERACTIVE_WORKER_BAT = SERVICES_DIR / "remove_interactive_worker_task.bat"
RUN_INTERACTIVE_WORKER_BAT = SERVICES_DIR / "run_interactive_worker.bat"
DIAGNOSTIC_INTERACTIVE_WORKER_BAT = SERVICES_DIR / "diagnostic_interactive_worker.bat"

INTERACTIVE_WORKER_TASK_NAME = "OrkaFlowInteractiveWorker"
INTERACTIVE_WORKER_DISPLAY_NAME = "OrkaFlow Interactive Worker"

DEFAULT_HTTP_TIMEOUT = 30
INTERACTIVE_AGENT_HEARTBEAT_TTL_SECONDS = 20
INTERACTIVE_AGENT_HEARTBEAT_INTERVAL_SECONDS = 5
INTERACTIVE_AGENT_POLL_INTERVAL_SECONDS = 2
INTERACTIVE_AGENT_RESULT_POLL_INTERVAL_SECONDS = 1
INTERACTIVE_AGENT_FOREGROUND_CONCURRENCY = 1
INTERACTIVE_AGENT_STALE_RESULT_TTL_HOURS = 24

EXECUTION_MODE_BACKGROUND = "background"
EXECUTION_MODE_FOREGROUND = "foreground"

RUNTIME_EVENT_PATH = "/api/v1/worker/runtime-events"
RUNTIME_EVENT_TYPE_FOREGROUND_TASK_SKIPPED = "foreground_task_skipped"
RUNTIME_EVENT_REASON_INTERACTIVE_AGENT_NOT_ACTIVE = "interactive_agent_not_active"
RUNTIME_EVENT_REASON_FOREGROUND_BUSY = "foreground_executor_busy"
RUNTIME_EVENT_REASON_INTERACTIVE_SESSION_UNAVAILABLE = "interactive_session_unavailable"
