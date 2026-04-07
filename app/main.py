# app\main.py

"""
Entrada principal do instalador.
"""

from app.core.logging_config import setup_logging
from app.ui.app_window import run_installer_app


def main() -> None:
    setup_logging()
    run_installer_app()


if __name__ == "__main__":
    main()
    