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

DEFAULT_HTTP_TIMEOUT = 30
