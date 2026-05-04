from __future__ import annotations

import re
from typing import Any

import requests
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.config_models import AuthData, BotsRegistry, RunnerData
from app.core.constants import AUTH_FILE, BOTS_REGISTRY_FILE, RUNNER_FILE
from app.core.json_store import load_model
from app.core.machine_info import get_machine_name
from app.diagnostics.prereq_checks import run_prerequisite_checks
from app.installer.runner_registration import InstallerInput, run_registration_flow


class UrlValidationThread(QThread):
    success = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url.strip().rstrip("/")

    def run(self) -> None:
        try:
            self.status.emit("Validando conectividade com a URL...")
            response = requests.get(self.base_url, timeout=10)

            if response.status_code < 500:
                self.success.emit()
            else:
                self.error.emit(f"Servidor respondeu HTTP {response.status_code}.")
        except Exception as exc:
            self.error.emit(f"Não foi possível acessar a URL: {exc}")


class RegistrationThread(QThread):
    success = Signal(dict)
    error = Signal(str)
    status = Signal(str)

    def __init__(self, installer_input: InstallerInput) -> None:
        super().__init__()
        self.installer_input = installer_input

    def run(self) -> None:
        try:
            result = run_registration_flow(
                self.installer_input,
                progress_callback=self.status.emit,
            )
            self.success.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class StepIndicator(QFrame):
    def __init__(self, number: int, title: str) -> None:
        super().__init__()

        self.number = number
        self.title = title
        self.active = False
        self.completed = False

        self.setObjectName("stepIndicator")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(8)

        self.circle = QLabel(str(number))
        self.circle.setFixedSize(24, 24)
        self.circle.setAlignment(Qt.AlignCenter)

        self.label = QLabel(title)
        self.label.setWordWrap(True)

        layout.addWidget(self.circle, 0, Qt.AlignTop)
        layout.addWidget(self.label, 1)

        self.refresh_style()

    def set_state(self, active: bool = False, completed: bool = False) -> None:
        self.active = active
        self.completed = completed
        self.refresh_style()

    def refresh_style(self) -> None:
        if self.completed:
            self.circle.setText("✓")
            self.circle.setStyleSheet(
                """
                background-color: #22c55e;
                color: white;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
                """
            )
            self.label.setStyleSheet("color: #ffffff; font-weight: 600; font-size: 12px;")
            return

        if self.active:
            self.circle.setText(str(self.number))
            self.circle.setStyleSheet(
                """
                background-color: #2563eb;
                color: white;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
                """
            )
            self.label.setStyleSheet("color: #ffffff; font-weight: 700; font-size: 12px;")
            return

        self.circle.setText(str(self.number))
        self.circle.setStyleSheet(
            """
            background-color: #1e293b;
            color: #9ca3af;
            border: 1px solid #334155;
            border-radius: 12px;
            font-weight: bold;
            font-size: 12px;
            """
        )
        self.label.setStyleSheet("color: #94a3b8; font-weight: 500; font-size: 12px;")


class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Assistente de Cadastro do Worker OrkaFlow")
        self.resize(1080, 720)
        self.setMinimumSize(920, 620)

        self.result_data: dict | None = None
        self.current_step = 0

        self.existing_auth: AuthData | None = None
        self.existing_runner: RunnerData | None = None
        self.existing_bots: BotsRegistry | None = None

        self._apply_styles()
        self._build_ui()
        self._load_initial_state()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #070d16;
                color: #f3f4f6;
                font-size: 12px;
            }

            QLabel#pageTitle {
                font-size: 24px;
                font-weight: 800;
                color: #ffffff;
            }

            QLabel#pageSubtitle {
                font-size: 12px;
                color: #9fb0c7;
            }

            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #ffffff;
            }

            QLabel#infoText {
                color: #cbd5e1;
                font-size: 12px;
            }

            QFrame#contentCard {
                background-color: #0d1728;
                border: 1px solid #1c2940;
                border-radius: 14px;
            }

            QFrame#sidePanel {
                background-color: #08142b;
                border-right: 1px solid #1c2940;
            }

            QFrame#stepIndicator {
                background-color: #081326;
                border: 1px solid #16243a;
                border-radius: 12px;
            }

            QLineEdit, QTextEdit {
                background-color: #07111f;
                border: 1px solid #21314b;
                border-radius: 8px;
                padding: 8px 10px;
                color: white;
            }

            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #3b82f6;
            }

            QTextEdit {
                font-size: 11px;
            }

            QPushButton {
                border-radius: 8px;
                padding: 6px 12px;
                min-height: 30px;
                font-weight: 600;
            }

            QPushButton#primaryButton {
                background-color: #2563eb;
                color: white;
                border: none;
            }

            QPushButton#primaryButton:hover {
                background-color: #1d4ed8;
            }

            QPushButton#secondaryButton {
                background-color: #13233a;
                color: #e5e7eb;
                border: 1px solid #28405f;
            }

            QPushButton#secondaryButton:hover {
                background-color: #19304d;
            }

            QPushButton:disabled {
                background-color: #16202c;
                color: #6b7280;
                border: 1px solid #243041;
            }

            QLabel#statusOk {
                color: #4ade80;
                font-weight: 700;
                font-size: 11px;
            }

            QLabel#statusError {
                color: #f87171;
                font-weight: 700;
                font-size: 11px;
            }

            QLabel#statusNeutral {
                color: #cbd5e1;
                font-size: 11px;
            }

            QLabel#statusWarning {
                color: #fbbf24;
                font-weight: 700;
                font-size: 11px;
            }

            QProgressBar {
                border: 1px solid #243041;
                border-radius: 7px;
                background-color: #0b1220;
                text-align: center;
                min-height: 12px;
            }

            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 6px;
            }

            QCheckBox {
                spacing: 6px;
                color: #dbe4f0;
                font-size: 11px;
            }
            """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_side_panel(), 0)
        root_layout.addWidget(self._build_main_area(), 1)

        self.setCentralWidget(root)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setMinimumWidth(230)
        panel.setMaximumWidth(230)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        logo = QLabel("OrkaFlow")
        logo.setStyleSheet("font-size: 18px; font-weight: 800; color: white;")

        subtitle = QLabel("Configuração simples do worker")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #94a3b8; font-size: 11px;")

        layout.addWidget(logo)
        layout.addWidget(subtitle)
        layout.addSpacing(4)

        self.step_widgets = [
            StepIndicator(1, "Boas-vindas"),
            StepIndicator(2, "Pré-requisitos"),
            StepIndicator(3, "URL do sistema"),
            StepIndicator(4, "Login e cadastro"),
            StepIndicator(5, "Worker pronto"),
        ]

        for step in self.step_widgets:
            layout.addWidget(step)

        layout.addStretch()

        footer = QLabel(
            "Ao final será gerado apenas um BAT para iniciar o worker no terminal do usuário."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #64748b; font-size: 10px;")

        layout.addWidget(footer)

        return panel

    def _build_main_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        self.page_title = QLabel("Assistente de Cadastro do Worker")
        self.page_title.setObjectName("pageTitle")
        self.page_title.setWordWrap(True)

        self.page_subtitle = QLabel("Configure esta máquina para executar atividades do OrkaFlow.")
        self.page_subtitle.setObjectName("pageSubtitle")
        self.page_subtitle.setWordWrap(True)

        layout.addWidget(self.page_title)
        layout.addWidget(self.page_subtitle)

        self.content_card = QFrame()
        self.content_card.setObjectName("contentCard")
        self.content_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        card_layout = QVBoxLayout(self.content_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.stack.addWidget(self._build_welcome_page())
        self.stack.addWidget(self._build_prereq_page())
        self.stack.addWidget(self._build_url_page())
        self.stack.addWidget(self._build_login_page())
        self.stack.addWidget(self._build_loading_page())
        self.stack.addWidget(self._build_dashboard_page())

        card_layout.addWidget(self.stack)
        layout.addWidget(self.content_card, 1)

        return container
    
    def _build_page_wrapper(self, title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setWordWrap(True)

        text_label = QLabel(text)
        text_label.setObjectName("infoText")
        text_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(text_label)

        return page, layout

    def _build_welcome_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Bem-vindo",
            "Este assistente vai preparar esta máquina para executar tasks diretamente no terminal."
        )

        layout.addStretch()

        btn = QPushButton("Começar")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(lambda: self._go_to_step(1))

        layout.addWidget(btn, alignment=Qt.AlignRight)

        return page

    def _build_prereq_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Pré-requisitos",
            "Validando Python, Git e ambiente."
        )

        self.prereq_output = QTextEdit()
        self.prereq_output.setReadOnly(True)

        layout.addWidget(self.prereq_output)

        btn_run = QPushButton("Validar")
        btn_run.setObjectName("primaryButton")
        btn_run.clicked.connect(self._run_prereq)

        layout.addWidget(btn_run, alignment=Qt.AlignRight)

        return page

    def _run_prereq(self) -> None:
        result = run_prerequisite_checks()
        text = ""

        all_ok = True

        for k, (ok, msg) in result.items():
            status = "OK" if ok else "ERRO"
            text += f"{k.upper()}: {status} - {msg}\n"

            if not ok:
                all_ok = False

        self.prereq_output.setText(text)

        if all_ok:
            self._go_to_step(2)
        else:
            QMessageBox.warning(
                self,
                "Pré-requisitos pendentes",
                "Existem pré-requisitos com erro. Corrija antes de continuar.",
            )

    def _build_url_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "URL do sistema",
            "Informe a URL do backend."
        )

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://localhost:8000")

        layout.addWidget(self.url_input)

        btn = QPushButton("Validar URL")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._validate_url)

        layout.addWidget(btn)

        return page

    def _validate_url(self) -> None:
        url = self.url_input.text().strip()

        try:
            r = requests.get(url, timeout=5)
            if r.status_code < 500:
                self._go_to_step(3)
            else:
                QMessageBox.warning(self, "Erro", "URL inválida")
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))

    def _build_login_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Login",
            "Informe credenciais para registrar o worker."
        )

        form = QFormLayout()

        self.login_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)

        self.runner_name = QLineEdit(get_machine_name())
        self.runner_label = QLineEdit()

        form.addRow("Login", self.login_input)
        form.addRow("Senha", self.password_input)
        form.addRow("Nome", self.runner_name)
        form.addRow("Label", self.runner_label)

        layout.addLayout(form)

        btn = QPushButton("Registrar")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._start_register)

        layout.addWidget(btn)

        return page

    def _start_register(self) -> None:
        data = InstallerInput(
            base_url=self.url_input.text(),
            login=self.login_input.text(),
            password=self.password_input.text(),
            runner_name=self.runner_name.text(),
            runner_label=self.runner_label.text(),
        )

        self._go_to_step(4)

        self.thread = RegistrationThread(data)
        self.thread.status.connect(self._update_loading)
        self.thread.success.connect(self._on_success)
        self.thread.error.connect(self._on_error)
        self.thread.start()

    def _build_loading_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Processando",
            "Aguarde..."
        )

        self.loading_label = QLabel("Iniciando...")
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)

        layout.addWidget(self.loading_label)
        layout.addWidget(self.loading_bar)

        return page

    def _update_loading(self, msg: str) -> None:
        self.loading_label.setText(msg)

    def _on_success(self, result: dict) -> None:
        self.result_data = result
        self._load_local_models()
        self._go_to_step(5)

    def _on_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Erro", msg)
        self._go_to_step(3)

    def _build_dashboard_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Worker pronto",
            "Agora é só executar o BAT."
        )

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)

        layout.addWidget(self.summary)

        return page

    def _load_initial_state(self) -> None:
        self._load_local_models()

        if self.existing_runner:
            self._go_to_step(5)
            self._update_dashboard()
        else:
            self._go_to_step(0)

    def _load_local_models(self) -> None:
        self.existing_auth = load_model(AUTH_FILE, AuthData)
        self.existing_runner = load_model(RUNNER_FILE, RunnerData)
        self.existing_bots = load_model(BOTS_REGISTRY_FILE, BotsRegistry)

    def _update_dashboard(self) -> None:
        if not self.existing_runner:
            return

        text = f"""
Runner: {self.existing_runner.name}
UUID: {self.existing_runner.uuid}

BAT:
C:\\OrkaFlow\\iniciar_worker.bat
"""

        if self.result_data:
            text += f"\nAtalho:\n{self.result_data.get('desktop_shortcut')}"

        if self.existing_bots and self.existing_bots.bots:
            text += "\n\nBots:\n"
            for b in self.existing_bots.bots:
                text += f"- {b.name} ({b.bot_id})\n"

        self.summary.setText(text)

    def _go_to_step(self, step: int) -> None:
        self.stack.setCurrentIndex(step)

        for i, w in enumerate(self.step_widgets):
            w.set_state(active=(i == step), completed=(i < step))

        if step == 5:
            self._update_dashboard()


def run_installer_app() -> None:
    app = QApplication([])
    font = QFont("Segoe UI", 9)
    app.setFont(font)

    window = InstallerWindow()
    window.show()

    app.exec()