from __future__ import annotations

from pathlib import Path

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
from app.core.constants import AUTH_FILE, BOTS_REGISTRY_FILE, RUNNER_FILE, WORKER_BAT_FILE
from app.core.json_store import load_model
from app.core.machine_info import get_machine_name
from app.diagnostics.prereq_checks import run_prerequisite_checks
from app.installer.runner_registration import InstallerInput, run_registration_flow


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
        self.setObjectName("stepIndicator")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(8)

        self.circle = QLabel(str(number))
        self.circle.setFixedSize(24, 24)
        self.circle.setAlignment(Qt.AlignCenter)

        self.label = QLabel(title)
        self.label.setWordWrap(True)

        layout.addWidget(self.circle)
        layout.addWidget(self.label, 1)

        self.set_state(False, False)

    def set_state(self, active: bool = False, completed: bool = False) -> None:
        if completed:
            self.circle.setText("✓")
            self.circle.setStyleSheet(
                "background:#22c55e;color:white;border-radius:12px;font-weight:700;"
            )
            self.label.setStyleSheet("color:white;font-weight:700;")
        elif active:
            self.circle.setText(str(self.number))
            self.circle.setStyleSheet(
                "background:#2563eb;color:white;border-radius:12px;font-weight:700;"
            )
            self.label.setStyleSheet("color:white;font-weight:700;")
        else:
            self.circle.setText(str(self.number))
            self.circle.setStyleSheet(
                "background:#1e293b;color:#94a3b8;border-radius:12px;font-weight:700;"
            )
            self.label.setStyleSheet("color:#94a3b8;font-weight:500;")


class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Assistente de Cadastro do Worker OrkaFlow")
        self.resize(1080, 720)
        self.setMinimumSize(920, 620)

        self.result_data: dict | None = None
        self.existing_auth: AuthData | None = None
        self.existing_runner: RunnerData | None = None
        self.existing_bots: BotsRegistry | None = None

        self._apply_styles()
        self._build_ui()
        self._load_initial_state()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background-color: #070d16;
                color: #f3f4f6;
                font-size: 12px;
            }

            QLabel#pageTitle {
                font-size: 24px;
                font-weight: 800;
                color: white;
            }

            QLabel#pageSubtitle {
                color: #9fb0c7;
            }

            QLabel#sectionTitle {
                font-size: 15px;
                font-weight: 800;
                color: white;
            }

            QLabel#infoText {
                color: #cbd5e1;
            }

            QFrame#sidePanel {
                background-color: #08142b;
                border-right: 1px solid #1c2940;
            }

            QFrame#contentCard {
                background-color: #0d1728;
                border: 1px solid #1c2940;
                border-radius: 14px;
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

            QPushButton {
                border-radius: 8px;
                padding: 8px 14px;
                min-height: 32px;
                font-weight: 700;
            }

            QPushButton#primaryButton {
                background-color: #2563eb;
                color: white;
                border: none;
            }

            QPushButton#secondaryButton {
                background-color: #13233a;
                color: #e5e7eb;
                border: 1px solid #28405f;
            }

            QProgressBar {
                border: 1px solid #243041;
                border-radius: 7px;
                background-color: #0b1220;
                min-height: 12px;
            }

            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 6px;
            }

            QCheckBox {
                color: #dbe4f0;
            }
            """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_side_panel())
        root_layout.addWidget(self._build_main_area(), 1)

        self.setCentralWidget(root)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setFixedWidth(240)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        logo = QLabel("OrkaFlow")
        logo.setStyleSheet("font-size:18px;font-weight:800;color:white;")

        subtitle = QLabel("Configuração simples do worker")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#94a3b8;font-size:11px;")

        layout.addWidget(logo)
        layout.addWidget(subtitle)
        layout.addSpacing(8)

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

        footer = QLabel("Ao final será gerado apenas um BAT para iniciar o worker.")
        footer.setWordWrap(True)
        footer.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(footer)

        return panel

    def _build_main_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        self.page_title = QLabel("Assistente de Cadastro do Worker")
        self.page_title.setObjectName("pageTitle")

        self.page_subtitle = QLabel("Configure esta máquina para executar atividades do OrkaFlow.")
        self.page_subtitle.setObjectName("pageSubtitle")

        layout.addWidget(self.page_title)
        layout.addWidget(self.page_subtitle)

        card = QFrame()
        card.setObjectName("contentCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome_page())
        self.stack.addWidget(self._build_prereq_page())
        self.stack.addWidget(self._build_url_page())
        self.stack.addWidget(self._build_login_page())
        self.stack.addWidget(self._build_loading_page())
        self.stack.addWidget(self._build_dashboard_page())

        card_layout.addWidget(self.stack)
        layout.addWidget(card, 1)

        return container

    def _build_page_wrapper(self, title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")

        text_label = QLabel(text)
        text_label.setObjectName("infoText")
        text_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(text_label)

        return page, layout

    def _build_welcome_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Bem-vindo",
            "Este assistente vai preparar esta máquina para executar tasks diretamente no terminal do usuário.",
        )

        info = QTextEdit()
        info.setReadOnly(True)
        info.setPlainText(
            "Fluxo:\n"
            "1. Validar pré-requisitos\n"
            "2. Informar URL do backend\n"
            "3. Fazer login\n"
            "4. Registrar/atualizar o runner\n"
            "5. Gerar o BAT e o atalho"
        )
        layout.addWidget(info, 1)

        btn = QPushButton("Começar")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(lambda: self._go_to_step(1))
        layout.addWidget(btn, alignment=Qt.AlignRight)

        return page

    def _build_prereq_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Pré-requisitos",
            "Clique em validar para conferir Python, Git e ambiente ODBC.",
        )

        self.prereq_output = QTextEdit()
        self.prereq_output.setReadOnly(True)
        layout.addWidget(self.prereq_output, 1)

        btn = QPushButton("Validar")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._run_prereq)
        layout.addWidget(btn, alignment=Qt.AlignRight)

        return page

    def _run_prereq(self) -> None:
        result = run_prerequisite_checks()

        all_ok = True
        lines: list[str] = []

        for key, (ok, msg) in result.items():
            lines.append(f"{key.upper()}: {'OK' if ok else 'ERRO'} - {msg}")
            if not ok:
                all_ok = False

        self.prereq_output.setPlainText("\n".join(lines))

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
            "Informe a URL base do backend do OrkaFlow.",
        )

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://localhost:8000")
        self.url_input.setText("http://localhost:8000")

        layout.addWidget(QLabel("URL base"))
        layout.addWidget(self.url_input)

        layout.addStretch()

        btn = QPushButton("Validar URL")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._validate_url)
        layout.addWidget(btn)

        return page

    def _validate_url(self) -> None:
        url = self.url_input.text().strip().rstrip("/")

        if not url:
            QMessageBox.warning(self, "URL obrigatória", "Informe a URL do backend.")
            return

        try:
            response = requests.get(url, timeout=5)
            if response.status_code < 500:
                self.url_input.setText(url)
                self._go_to_step(3)
                return

            QMessageBox.warning(self, "Erro", f"Servidor respondeu HTTP {response.status_code}.")
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _build_login_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Login e cadastro",
            "Informe as credenciais e os dados do runner.",
        )

        form_box = QFrame()
        form_box.setObjectName("contentCard")
        form_layout_outer = QVBoxLayout(form_box)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        machine_name = get_machine_name()

        self.login_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)

        self.runner_name = QLineEdit(machine_name)
        self.runner_label = QLineEdit(machine_name)
        self.access_remote_checkbox = QCheckBox("Permitir acesso remoto")

        form.addRow("Login:", self.login_input)
        form.addRow("Senha:", self.password_input)
        form.addRow("Nome:", self.runner_name)
        form.addRow("Label:", self.runner_label)
        form.addRow("", self.access_remote_checkbox)

        form_layout_outer.addLayout(form)
        layout.addWidget(form_box)

        layout.addStretch()

        btn = QPushButton("Registrar")
        btn.setObjectName("primaryButton")
        btn.clicked.connect(self._start_register)
        layout.addWidget(btn)

        return page

    def _start_register(self) -> None:
        base_url = self.url_input.text().strip().rstrip("/")
        login = self.login_input.text().strip()
        password = self.password_input.text()
        runner_name = self.runner_name.text().strip()
        runner_label = self.runner_label.text().strip()

        if not base_url or not login or not password or not runner_name:
            QMessageBox.warning(
                self,
                "Dados obrigatórios",
                "Preencha URL, login, senha e nome do runner.",
            )
            return

        if not runner_label:
            runner_label = runner_name
            self.runner_label.setText(runner_label)

        data = InstallerInput(
            base_url=base_url,
            login=login,
            password=password,
            runner_name=runner_name,
            runner_label=runner_label,
            access_remote=self.access_remote_checkbox.isChecked(),
        )

        self._go_to_step(4)

        self.registration_thread = RegistrationThread(data)
        self.registration_thread.status.connect(self._update_loading)
        self.registration_thread.success.connect(self._on_success)
        self.registration_thread.error.connect(self._on_error)
        self.registration_thread.start()

    def _build_loading_page(self) -> QWidget:
        page, layout = self._build_page_wrapper("Processando", "Aguarde...")

        self.loading_label = QLabel("Iniciando...")
        self.loading_label.setObjectName("infoText")

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)

        layout.addStretch()
        layout.addWidget(self.loading_label)
        layout.addWidget(self.loading_bar)
        layout.addStretch()

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
            "Cadastro concluído. Agora execute o BAT para iniciar o worker.",
        )

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary, 1)

        return page

    def _load_initial_state(self) -> None:
        self._load_local_models()

        if self.existing_runner:
            self._go_to_step(5)
        else:
            self._go_to_step(0)

    def _load_local_models(self) -> None:
        self.existing_auth = load_model(AUTH_FILE, AuthData)
        self.existing_runner = load_model(RUNNER_FILE, RunnerData)
        self.existing_bots = load_model(BOTS_REGISTRY_FILE, BotsRegistry) or BotsRegistry()

    def _update_dashboard(self) -> None:
        self._load_local_models()

        if not self.existing_runner:
            self.summary.setPlainText("Runner ainda não configurado.")
            return

        bat_path = str(WORKER_BAT_FILE)

        shortcut = None
        if self.result_data:
            shortcut = self.result_data.get("desktop_shortcut") or self.result_data.get("shortcut")

        lines = [
            f"Runner: {self.existing_runner.name}",
            f"Label: {self.existing_runner.label}",
            f"UUID: {self.existing_runner.uuid}",
            "",
            "BAT:",
            bat_path,
        ]

        if shortcut:
            lines.extend(["", "Atalho:", str(shortcut)])

        if self.existing_bots and self.existing_bots.bots:
            lines.extend(["", "Bots vinculados:"])
            for bot in self.existing_bots.bots:
                lines.append(f"- {bot.name or 'Sem nome'} | bot_id={bot.bot_id}")

        self.summary.setPlainText("\n".join(lines))

    def _go_to_step(self, step: int) -> None:
        self.stack.setCurrentIndex(step)

        for index, widget in enumerate(self.step_widgets):
            widget.set_state(active=(index == step), completed=(index < step))

        if step == 5:
            self._update_dashboard()


def run_installer_app() -> None:
    app = QApplication.instance() or QApplication([])

    font = QFont("Segoe UI", 9)
    app.setFont(font)

    window = InstallerWindow()
    window.show()

    app.exec()
    