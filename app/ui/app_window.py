# app\ui\app_window.py

from __future__ import annotations

import requests
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
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
from app.service.nssm_manager import (
    get_service_status,
    install_service,
    restart_service,
    start_service,
    stop_service,
)
from app.service.service_files import generate_service_files


class UrlValidationThread(QThread):
    success = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url.strip().rstrip("/")

    def run(self) -> None:
        try:
            self.status.emit("Validando conectividade com a URL informada...")
            response = requests.get(self.base_url, timeout=10)

            if response.status_code < 500:
                self.success.emit()
            else:
                self.error.emit(f"Servidor respondeu com HTTP {response.status_code}.")
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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.circle = QLabel(str(number))
        self.circle.setFixedSize(28, 28)
        self.circle.setAlignment(Qt.AlignCenter)
        self.circle.setObjectName("stepCircle")

        self.label = QLabel(title)
        self.label.setObjectName("stepLabel")

        layout.addWidget(self.circle)
        layout.addWidget(self.label)
        layout.addStretch()

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
                border-radius: 14px;
                font-weight: bold;
            """
            )
            self.label.setStyleSheet("color: #e5e7eb; font-weight: 600;")
        elif self.active:
            self.circle.setText(str(self.number))
            self.circle.setStyleSheet(
                """
                background-color: #2563eb;
                color: white;
                border-radius: 14px;
                font-weight: bold;
            """
            )
            self.label.setStyleSheet("color: white; font-weight: 700;")
        else:
            self.circle.setText(str(self.number))
            self.circle.setStyleSheet(
                """
                background-color: #2a2f3a;
                color: #9ca3af;
                border: 1px solid #3b4252;
                border-radius: 14px;
                font-weight: bold;
            """
            )
            self.label.setStyleSheet("color: #9ca3af; font-weight: 500;")


class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Assistente de Cadastro do Worker OrkaFlow")
        self.setMinimumSize(1040, 720)

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
                background-color: #0f1115;
                color: #f3f4f6;
                font-size: 13px;
            }

            QLabel#pageTitle {
                font-size: 26px;
                font-weight: 700;
                color: #ffffff;
            }

            QLabel#pageSubtitle {
                font-size: 13px;
                color: #9ca3af;
            }

            QLabel#sectionTitle {
                font-size: 18px;
                font-weight: 700;
                color: #ffffff;
            }

            QLabel#infoText {
                color: #cbd5e1;
                line-height: 1.4em;
            }

            QFrame#contentCard {
                background-color: #171a21;
                border: 1px solid #262b36;
                border-radius: 18px;
            }

            QFrame#sidePanel {
                background-color: #141821;
                border-right: 1px solid #232834;
            }

            QLineEdit, QTextEdit {
                background-color: #0f131a;
                border: 1px solid #2b3240;
                border-radius: 10px;
                padding: 10px 12px;
                color: white;
                min-height: 18px;
            }

            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #3b82f6;
            }

            QTextEdit {
                padding: 8px 10px;
            }

            QPushButton {
                border-radius: 10px;
                padding: 10px 16px;
                min-height: 20px;
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
                background-color: #1f2430;
                color: #e5e7eb;
                border: 1px solid #313846;
            }

            QPushButton#secondaryButton:hover {
                background-color: #262d3a;
            }

            QPushButton#successButton {
                background-color: #16a34a;
                color: white;
                border: none;
            }

            QPushButton#successButton:hover {
                background-color: #15803d;
            }

            QLabel#statusOk {
                color: #4ade80;
                font-weight: 600;
            }

            QLabel#statusError {
                color: #f87171;
                font-weight: 600;
            }

            QLabel#statusNeutral {
                color: #cbd5e1;
            }

            QLabel#statusWarning {
                color: #fbbf24;
                font-weight: 600;
            }

            QProgressBar {
                border: 1px solid #2b3240;
                border-radius: 8px;
                background-color: #0f131a;
                text-align: center;
                min-height: 14px;
            }

            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 7px;
            }

            QCheckBox {
                spacing: 8px;
            }
        """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.side_panel = self._build_side_panel()
        self.main_area = self._build_main_area()

        root_layout.addWidget(self.side_panel, 0)
        root_layout.addWidget(self.main_area, 1)

        self.setCentralWidget(root)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setFixedWidth(280)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(18)

        logo = QLabel("OrkaFlow")
        logo.setStyleSheet("font-size: 22px; font-weight: 800; color: white;")

        subtitle = QLabel("Assistente de configuração do worker")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #9ca3af; font-size: 13px;")

        layout.addWidget(logo)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.step_widgets = [
            StepIndicator(1, "Boas-vindas"),
            StepIndicator(2, "Pré-requisitos"),
            StepIndicator(3, "URL do sistema"),
            StepIndicator(4, "Login e cadastro"),
            StepIndicator(5, "Painel do worker"),
        ]

        for step in self.step_widgets:
            layout.addWidget(step)

        layout.addStretch()

        footer = QLabel(
            "Este assistente registra a máquina, prepara o ambiente local e permite controlar o serviço do worker."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(footer)

        return panel

    def _build_main_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(18)

        self.page_title = QLabel("Assistente de Cadastro do Worker")
        self.page_title.setObjectName("pageTitle")

        self.page_subtitle = QLabel(
            "Configure este computador para operar como worker do OrkaFlow."
        )
        self.page_subtitle.setObjectName("pageSubtitle")
        self.page_subtitle.setWordWrap(True)

        layout.addWidget(self.page_title)
        layout.addWidget(self.page_subtitle)

        self.content_card = QFrame()
        self.content_card.setObjectName("contentCard")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        self.content_card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.content_card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(20)

        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack)

        self.stack.addWidget(self._build_welcome_page())
        self.stack.addWidget(self._build_prereq_page())
        self.stack.addWidget(self._build_url_page())
        self.stack.addWidget(self._build_login_page())
        self.stack.addWidget(self._build_loading_page())
        self.stack.addWidget(self._build_dashboard_page())

        layout.addWidget(self.content_card, 1)

        return container

    def _build_page_wrapper(self, title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

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
            "Este assistente vai validar os requisitos da máquina, conectar ao sistema, autenticar o usuário e registrar este computador como worker.",
        )

        info_box = self._make_info_box(
            [
                "Validação de Python, Git e ambiente ODBC",
                "Teste de conectividade com a URL do sistema",
                "Login com usuário autorizado",
                "Registro do runner no backend",
                "Preparação dos arquivos locais e tentativa de instalação do serviço",
            ]
        )
        layout.addWidget(info_box)
        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()

        start_button = self._make_button("Começar configuração", "primaryButton")
        start_button.clicked.connect(lambda: self._go_to_step(1))

        buttons.addWidget(start_button)
        layout.addLayout(buttons)

        return page

    def _build_prereq_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Verificação de pré-requisitos",
            "Antes do cadastro, o assistente precisa confirmar que a máquina está pronta. O Python é obrigatório para os processos do worker.",
        )

        self.python_status = QLabel("Python: aguardando verificação")
        self.python_status.setObjectName("statusNeutral")

        self.git_status = QLabel("Git: aguardando verificação")
        self.git_status.setObjectName("statusNeutral")

        self.driver_status = QLabel("Driver: aguardando verificação")
        self.driver_status.setObjectName("statusNeutral")

        self.prereq_message = QLabel("Clique em executar verificação para continuar.")
        self.prereq_message.setWordWrap(True)
        self.prereq_message.setObjectName("statusNeutral")

        status_box = self._make_info_box([])
        status_layout = status_box.layout()
        status_layout.addWidget(self.python_status)
        status_layout.addWidget(self.git_status)
        status_layout.addWidget(self.driver_status)
        status_layout.addWidget(self.prereq_message)

        layout.addWidget(status_box)
        layout.addStretch()

        buttons = QHBoxLayout()
        back_button = self._make_button("Voltar", "secondaryButton")
        run_button = self._make_button("Executar verificação", "primaryButton")
        self.prereq_next_button = self._make_button("Continuar", "primaryButton")
        self.prereq_next_button.setEnabled(False)

        back_button.clicked.connect(lambda: self._go_to_step(0))
        run_button.clicked.connect(self._run_prereq_checks)
        self.prereq_next_button.clicked.connect(lambda: self._go_to_step(2))

        buttons.addWidget(back_button)
        buttons.addStretch()
        buttons.addWidget(run_button)
        buttons.addWidget(self.prereq_next_button)
        layout.addLayout(buttons)

        return page

    def _build_url_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "URL do sistema",
            "Informe a URL base do backend do OrkaFlow para validar a conectividade antes do login.",
        )

        form_box = self._make_info_box([])
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(14)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://127.0.0.1:8000")

        self.url_status = QLabel("Aguardando validação da URL.")
        self.url_status.setWordWrap(True)
        self.url_status.setObjectName("statusNeutral")

        form_layout.addRow("URL base do sistema:", self.url_input)

        form_box.layout().addLayout(form_layout)
        form_box.layout().addWidget(self.url_status)

        layout.addWidget(form_box)
        layout.addStretch()

        buttons = QHBoxLayout()
        back_button = self._make_button("Voltar", "secondaryButton")
        validate_button = self._make_button("Validar URL", "primaryButton")
        self.url_next_button = self._make_button("Continuar", "primaryButton")
        self.url_next_button.setEnabled(False)

        back_button.clicked.connect(lambda: self._go_to_step(1))
        validate_button.clicked.connect(self._start_url_validation)
        self.url_next_button.clicked.connect(lambda: self._go_to_step(3))

        buttons.addWidget(back_button)
        buttons.addStretch()
        buttons.addWidget(validate_button)
        buttons.addWidget(self.url_next_button)
        layout.addLayout(buttons)

        return page

    def _build_login_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Login e cadastro do worker",
            "Informe as credenciais do sistema e os dados deste worker para concluir o registro.",
        )

        form_box = self._make_info_box([])
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(14)

        self.login_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)

        self.runner_name_input = QLineEdit()
        self.runner_name_input.setText(get_machine_name())

        self.runner_label_input = QLineEdit()
        self.runner_label_input.setPlaceholderText("Ex.: Máquina para rodar SAP")

        self.access_remote_checkbox = QCheckBox("Permitir acesso remoto")

        self.login_status = QLabel("Preencha os dados e inicie o cadastro.")
        self.login_status.setWordWrap(True)
        self.login_status.setObjectName("statusNeutral")

        form_layout.addRow("Login:", self.login_input)
        form_layout.addRow("Senha:", self.password_input)
        form_layout.addRow("Nome do worker:", self.runner_name_input)
        form_layout.addRow("Label do worker:", self.runner_label_input)
        form_layout.addRow("", self.access_remote_checkbox)

        form_box.layout().addLayout(form_layout)
        form_box.layout().addWidget(self.login_status)

        layout.addWidget(form_box)
        layout.addStretch()

        buttons = QHBoxLayout()
        back_button = self._make_button("Voltar", "secondaryButton")
        register_button = self._make_button("Autenticar e cadastrar", "primaryButton")

        back_button.clicked.connect(lambda: self._go_to_step(2))
        register_button.clicked.connect(self._start_registration)

        buttons.addWidget(back_button)
        buttons.addStretch()
        buttons.addWidget(register_button)
        layout.addLayout(buttons)

        return page

    def _build_loading_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Processando",
            "O assistente está executando as etapas necessárias. Aguarde alguns instantes.",
        )

        box = self._make_info_box([])
        self.loading_status = QLabel("Aguarde...")
        self.loading_status.setAlignment(Qt.AlignCenter)
        self.loading_status.setWordWrap(True)
        self.loading_status.setStyleSheet("font-size: 15px; font-weight: 600; color: white;")

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)

        box.layout().addWidget(self.loading_status)
        box.layout().addSpacing(8)
        box.layout().addWidget(self.loading_bar)

        layout.addStretch()
        layout.addWidget(box)
        layout.addStretch()

        return page

    def _build_dashboard_page(self) -> QWidget:
        page, layout = self._build_page_wrapper(
            "Painel do worker",
            "Esta máquina já está configurada como runner. Aqui você acompanha os dados locais, os bots registrados e o estado do serviço do Windows.",
        )

        self.dashboard_summary = QLabel("Aguardando leitura da configuração local...")
        self.dashboard_summary.setWordWrap(True)
        self.dashboard_summary.setObjectName("infoText")

        self.dashboard_config_status = QLabel("Status local: aguardando")
        self.dashboard_config_status.setObjectName("statusNeutral")

        self.dashboard_service_status = QLabel("Serviço: aguardando consulta")
        self.dashboard_service_status.setObjectName("statusNeutral")

        self.dashboard_bots = QTextEdit()
        self.dashboard_bots.setReadOnly(True)
        self.dashboard_bots.setMinimumHeight(180)

        self.dashboard_operation = QLabel("Nenhuma operação executada ainda.")
        self.dashboard_operation.setWordWrap(True)
        self.dashboard_operation.setObjectName("statusNeutral")

        summary_box = self._make_info_box([])
        summary_box.layout().addWidget(self.dashboard_summary)
        summary_box.layout().addWidget(self.dashboard_config_status)
        summary_box.layout().addWidget(self.dashboard_service_status)

        bots_box = self._make_info_box([])
        bots_title = QLabel("Bots registrados nesta máquina")
        bots_title.setObjectName("sectionTitle")
        bots_box.layout().addWidget(bots_title)
        bots_box.layout().addWidget(self.dashboard_bots)

        operation_box = self._make_info_box([])
        operation_box.layout().addWidget(self.dashboard_operation)

        layout.addWidget(summary_box)
        layout.addWidget(bots_box)
        layout.addWidget(operation_box)
        layout.addStretch()

        buttons = QHBoxLayout()
        self.dashboard_refresh_button = self._make_button("Atualizar status", "secondaryButton")
        self.dashboard_install_button = self._make_button("Instalar serviço", "successButton")
        self.dashboard_start_button = self._make_button("Iniciar serviço", "primaryButton")
        self.dashboard_stop_button = self._make_button("Parar serviço", "secondaryButton")
        self.dashboard_restart_button = self._make_button("Reiniciar serviço", "primaryButton")
        close_button = self._make_button("Fechar", "secondaryButton")

        self.dashboard_refresh_button.clicked.connect(self._refresh_dashboard)
        self.dashboard_install_button.clicked.connect(self._install_service_from_dashboard)
        self.dashboard_start_button.clicked.connect(lambda: self._execute_service_action("start"))
        self.dashboard_stop_button.clicked.connect(lambda: self._execute_service_action("stop"))
        self.dashboard_restart_button.clicked.connect(lambda: self._execute_service_action("restart"))
        close_button.clicked.connect(self.close)

        buttons.addWidget(self.dashboard_refresh_button)
        buttons.addStretch()
        buttons.addWidget(self.dashboard_install_button)
        buttons.addWidget(self.dashboard_start_button)
        buttons.addWidget(self.dashboard_stop_button)
        buttons.addWidget(self.dashboard_restart_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        return page

    def _make_info_box(self, lines: list[str]) -> QFrame:
        box = QFrame()
        box.setObjectName("contentCard")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        for line in lines:
            label = QLabel(f"• {line}")
            label.setWordWrap(True)
            label.setObjectName("infoText")
            layout.addWidget(label)

        return box

    def _make_button(self, text: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _load_initial_state(self) -> None:
        self._load_local_models()

        if self._is_machine_already_configured():
            self._refresh_dashboard(operation_message="Configuração existente detectada. Exibindo painel do worker.")
            self._go_to_step(5)
            return

        self._set_step(0)
        self.stack.setCurrentIndex(0)

    def _load_local_models(self) -> None:
        self.existing_auth = load_model(AUTH_FILE, AuthData)
        self.existing_runner = load_model(RUNNER_FILE, RunnerData)
        self.existing_bots = load_model(BOTS_REGISTRY_FILE, BotsRegistry) or BotsRegistry()

    def _is_machine_already_configured(self) -> bool:
        return self.existing_auth is not None and self.existing_runner is not None

    def _set_step(self, step: int) -> None:
        self.current_step = step

        for index, widget in enumerate(self.step_widgets):
            widget.set_state(active=(index == step), completed=(index < step))

        titles = {
            0: ("Assistente de Cadastro do Worker", "Inicie a configuração deste computador."),
            1: ("Pré-requisitos", "Valide o ambiente antes de prosseguir."),
            2: ("Conexão com o sistema", "Teste a URL base do backend."),
            3: ("Autenticação e cadastro", "Informe as credenciais e registre o worker."),
            4: ("Processando", "O assistente está executando as ações necessárias."),
            5: ("Painel do worker", "Visualize o runner configurado e controle o serviço do Windows."),
        }

        title, subtitle = titles.get(step, ("OrkaFlow Worker", ""))
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)

    def _go_to_step(self, step: int) -> None:
        stack_index_map = {
            0: 0,
            1: 1,
            2: 2,
            3: 3,
            4: 4,
            5: 5,
        }
        self.stack.setCurrentIndex(stack_index_map[step])
        self._set_step(step)

    def _run_prereq_checks(self) -> None:
        checks = run_prerequisite_checks()

        python_ok, python_msg = checks["python"]
        git_ok, git_msg = checks["git"]
        driver_ok, driver_msg = checks["driver"]

        self._set_status_label(self.python_status, f"{'✅' if python_ok else '❌'} Python: {python_msg}", python_ok)
        self._set_status_label(self.git_status, f"{'✅' if git_ok else '❌'} Git: {git_msg}", git_ok)
        self._set_status_label(self.driver_status, f"{'✅' if driver_ok else '❌'} Driver: {driver_msg}", driver_ok)

        if python_ok and git_ok and driver_ok:
            self.prereq_message.setText("Pré-requisitos validados com sucesso. Você já pode continuar.")
            self.prereq_message.setObjectName("statusOk")
            self.prereq_next_button.setEnabled(True)
        else:
            self.prereq_message.setText(
                "Existem pendências no ambiente. O Python é obrigatório e os demais itens também precisam estar disponíveis para seguir."
            )
            self.prereq_message.setObjectName("statusError")
            self.prereq_next_button.setEnabled(False)

        self.prereq_message.style().unpolish(self.prereq_message)
        self.prereq_message.style().polish(self.prereq_message)

    def _set_status_label(self, label: QLabel, text: str, ok: bool | None = None, warning: bool = False) -> None:
        label.setText(text)
        if warning:
            label.setObjectName("statusWarning")
        elif ok is True:
            label.setObjectName("statusOk")
        elif ok is False:
            label.setObjectName("statusError")
        else:
            label.setObjectName("statusNeutral")
        label.style().unpolish(label)
        label.style().polish(label)

    def _start_url_validation(self) -> None:
        base_url = self.url_input.text().strip()

        if not base_url:
            self._set_status_label(self.url_status, "Informe a URL base do sistema.", ok=False)
            return

        self._go_to_step(4)
        self.loading_status.setText("Validando URL do sistema...")

        self.url_thread = UrlValidationThread(base_url)
        self.url_thread.status.connect(self.loading_status.setText)
        self.url_thread.success.connect(self._on_url_validation_success)
        self.url_thread.error.connect(self._on_url_validation_error)
        self.url_thread.start()

    def _on_url_validation_success(self) -> None:
        self._set_status_label(self.url_status, "URL validada com sucesso.", ok=True)
        self.url_next_button.setEnabled(True)
        self._go_to_step(2)

    def _on_url_validation_error(self, message: str) -> None:
        self._set_status_label(self.url_status, message, ok=False)
        self.url_next_button.setEnabled(False)
        self._go_to_step(2)

    def _start_registration(self) -> None:
        installer_input = InstallerInput(
            base_url=self.url_input.text().strip(),
            login=self.login_input.text().strip(),
            password=self.password_input.text(),
            runner_name=self.runner_name_input.text().strip(),
            runner_label=self.runner_label_input.text().strip(),
            access_remote=self.access_remote_checkbox.isChecked(),
        )

        self._go_to_step(4)
        self.loading_status.setText("Iniciando cadastro do worker...")

        self.registration_thread = RegistrationThread(installer_input)
        self.registration_thread.status.connect(self.loading_status.setText)
        self.registration_thread.success.connect(self._on_registration_success)
        self.registration_thread.error.connect(self._on_registration_error)
        self.registration_thread.start()

    def _on_registration_success(self, result: dict) -> None:
        self.result_data = result
        self._load_local_models()

        install_messages: list[str] = []

        try:
            files = generate_service_files()
            install_messages.append(
                "Arquivos do serviço gerados com sucesso em: "
                f"{files['install_service_bat']}"
            )
        except Exception as exc:
            install_messages.append(f"Falha ao gerar arquivos do serviço: {exc}")
            self._refresh_dashboard(operation_message="\n".join(install_messages))
            self._go_to_step(5)
            return

        success, output = install_service()
        if success:
            install_messages.append("Serviço instalado com sucesso.")
            install_messages.append(output or "Instalação concluída sem detalhes adicionais.")
        else:
            install_messages.append("Não foi possível instalar o serviço automaticamente.")
            install_messages.append(output or "Nenhum detalhe retornado.")

        self._refresh_dashboard(operation_message="\n\n".join(install_messages))
        self._go_to_step(5)

    def _on_registration_error(self, message: str) -> None:
        self.login_status.setText(f"Erro: {message}")
        self.login_status.setObjectName("statusError")
        self.login_status.style().unpolish(self.login_status)
        self.login_status.style().polish(self.login_status)
        self._go_to_step(3)
        QMessageBox.critical(self, "Erro no cadastro", message)

    def _refresh_dashboard(self, operation_message: str | None = None) -> None:
        self._load_local_models()

        if not self._is_machine_already_configured() or self.existing_runner is None:
            self.dashboard_summary.setText(
                "Esta máquina ainda não possui auth.json e runner.json válidos. Faça o cadastro pelo assistente."
            )
            self._set_status_label(self.dashboard_config_status, "Status local: worker não configurado.", ok=False)
            self._set_status_label(self.dashboard_service_status, "Serviço: não consultado.", ok=None)
            self.dashboard_bots.setPlainText("Nenhum bot disponível porque o worker ainda não foi configurado.")
            self.dashboard_install_button.setEnabled(False)
            self.dashboard_start_button.setEnabled(False)
            self.dashboard_stop_button.setEnabled(False)
            self.dashboard_restart_button.setEnabled(False)
            if operation_message:
                self._set_status_label(self.dashboard_operation, operation_message, ok=False)
            return

        runner = self.existing_runner
        auth = self.existing_auth
        bots_registry = self.existing_bots or BotsRegistry()

        self.dashboard_summary.setText(
            f"Máquina: {runner.host_name}\n"
            f"Nome do runner: {runner.name}\n"
            f"Label: {runner.label}\n"
            f"ID: {runner.id}\n"
            f"UUID: {runner.uuid}\n"
            f"Backend: {auth.base_url if auth else '-'}"
        )
        self._set_status_label(
            self.dashboard_config_status,
            "Status local: esta máquina já está configurada como runner.",
            ok=True,
        )

        self._update_bots_text(bots_registry)
        self._update_service_status()

        self.dashboard_install_button.setEnabled(True)

        if operation_message:
            self._set_status_label(self.dashboard_operation, operation_message, ok=True)
        else:
            self._set_status_label(self.dashboard_operation, "Status atualizado com sucesso.", ok=True)

    def _update_bots_text(self, bots_registry: BotsRegistry) -> None:
        if not bots_registry.bots:
            self.dashboard_bots.setPlainText("Nenhum bot registrado localmente nesta máquina.")
            return

        lines: list[str] = []
        for index, bot in enumerate(bots_registry.bots, start=1):
            lines.append(
                f"{index}. {bot.name or 'Sem nome'} | bot_id={bot.bot_id} | "
                f"versão={bot.installed_version or '-'} | status_instalação={bot.last_install_status or '-'}"
            )
            if bot.local_path:
                lines.append(f"   local_path: {bot.local_path}")
            if bot.venv_path:
                lines.append(f"   venv_path: {bot.venv_path}")
            if bot.repository_url:
                lines.append(f"   repository_url: {bot.repository_url}")

        self.dashboard_bots.setPlainText("\n".join(lines))

    def _update_service_status(self) -> None:
        status = get_service_status()

        if status.state == "running":
            self._set_status_label(self.dashboard_service_status, "Serviço: em execução.", ok=True)
            self.dashboard_start_button.setEnabled(False)
            self.dashboard_stop_button.setEnabled(True)
            self.dashboard_restart_button.setEnabled(True)
        elif status.state == "not_installed":
            self._set_status_label(
                self.dashboard_service_status,
                "Serviço: ainda não instalado nesta máquina.",
                ok=None,
                warning=True,
            )
            self.dashboard_start_button.setEnabled(False)
            self.dashboard_stop_button.setEnabled(False)
            self.dashboard_restart_button.setEnabled(False)
        elif status.state in {"stopped", "unknown", "start_pending", "stop_pending"}:
            self._set_status_label(
                self.dashboard_service_status,
                f"Serviço: {status.state}.",
                ok=None,
                warning=status.state in {"start_pending", "stop_pending", "unknown"},
            )
            self.dashboard_start_button.setEnabled(status.state != "start_pending")
            self.dashboard_stop_button.setEnabled(status.state not in {"stopped", "stop_pending"})
            self.dashboard_restart_button.setEnabled(status.state not in {"start_pending", "stop_pending"})
        else:
            self._set_status_label(self.dashboard_service_status, f"Serviço: {status.state}.", ok=None, warning=True)
            self.dashboard_start_button.setEnabled(True)
            self.dashboard_stop_button.setEnabled(True)
            self.dashboard_restart_button.setEnabled(True)

    def _install_service_from_dashboard(self) -> None:
        try:
            files = generate_service_files()
            success, output = install_service()
            message = (
                f"Arquivos do serviço gerados em {files['install_service_bat']}.\n\n"
                + (output or "Nenhum detalhe retornado.")
            )
            self._refresh_dashboard(operation_message=message)
            if success:
                QMessageBox.information(self, "Serviço instalado", "Serviço instalado ou atualizado com sucesso.")
            else:
                QMessageBox.warning(self, "Falha na instalação", message)
        except Exception as exc:
            self._refresh_dashboard(operation_message=f"Erro ao instalar serviço: {exc}")
            QMessageBox.critical(self, "Erro ao instalar serviço", str(exc))

    def _execute_service_action(self, action: str) -> None:
        actions = {
            "start": start_service,
            "stop": stop_service,
            "restart": restart_service,
        }

        func = actions[action]
        success, output = func()
        self._refresh_dashboard(operation_message=output or f"Operação {action} executada.")

        if success:
            QMessageBox.information(self, "Operação concluída", output or "Operação concluída com sucesso.")
        else:
            QMessageBox.warning(self, "Operação com retorno de erro", output or "A operação não retornou sucesso.")



def run_installer_app() -> None:
    app = QApplication.instance() or QApplication([])

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = InstallerWindow()
    window.show()
    app.exec()
