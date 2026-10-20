"""
Assistente de configuração do worker (tkinter, já vem com o Python).

Abra pelo configurar_worker.bat. Pede a URL da API, o login do painel e os
dados da máquina, registra o runner no backend (se o nome já existir, ele é
atualizado), baixa o runtime, cria o iniciar_worker.bat e o atalho na área de
trabalho. Todo o andamento e qualquer erro aparecem na área de log e também em
LOGS_DIR/setup.log.
"""

from __future__ import annotations

import os
import queue
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk

from app.core.config_models import AuthData, RunnerData
from app.core.constants import APP_NAME, AUTH_FILE, LOGS_DIR, RUNNER_FILE, WORKER_BAT_FILE, WORKER_VERSION
from app.core.json_store import load_model
from app.core.machine_info import get_machine_name
from app.installer.runner_registration import InstallerInput, run_registration_flow

DEFAULT_API_URL = "http://10.50.156.202:8008"
SETUP_LOG_FILE = LOGS_DIR / "setup.log"


class SetupWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} - Configurar máquina (v{WORKER_VERSION})")
        self.root.geometry("760x640")
        self.root.minsize(680, 560)

        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._shortcut: str | None = None

        existing_auth = self._safe_load(AUTH_FILE, AuthData)
        existing_runner = self._safe_load(RUNNER_FILE, RunnerData)
        host = get_machine_name().upper()

        self.var_url = tk.StringVar(value=existing_auth.base_url if existing_auth else DEFAULT_API_URL)
        self.var_login = tk.StringVar(value=existing_auth.login if existing_auth else "")
        self.var_password = tk.StringVar()
        self.var_name = tk.StringVar(value=existing_runner.name if existing_runner else host)
        self.var_label = tk.StringVar(value=existing_runner.label if existing_runner else host)
        self.var_remote = tk.BooleanVar(value=existing_runner.access_remote if existing_runner else False)
        self.var_admin = tk.BooleanVar(value=True)
        self.var_start = tk.BooleanVar(value=True)

        self._build()
        self._log(f"{APP_NAME} v{WORKER_VERSION} - pasta base: {WORKER_BAT_FILE.parent}")
        if existing_runner:
            self._log(
                f"Esta máquina já tem uma configuração (runner '{existing_runner.name}'). "
                "Configurar de novo atualiza o runner existente."
            )
        self.root.after(150, self._drain_events)

    # --- construção da tela -------------------------------------------------

    @staticmethod
    def _safe_load(path, model):
        try:
            return load_model(path, model)
        except Exception:
            return None

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(frame, text="Dados da configuração", padding=10)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        rows = [
            ("URL da API", self.var_url, False),
            ("Login do painel", self.var_login, False),
            ("Senha do painel", self.var_password, True),
            ("Nome do runner", self.var_name, False),
            ("Rótulo (descrição)", self.var_label, False),
        ]
        self._entries: list[ttk.Entry] = []

        for index, (label, variable, secret) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=3, padx=(0, 10))
            entry = ttk.Entry(form, textvariable=variable, show="*" if secret else "")
            entry.grid(row=index, column=1, sticky="ew", pady=3)
            self._entries.append(entry)

        options = ttk.Frame(form)
        options.grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(options, text="Acesso remoto liberado", variable=self.var_remote).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="Atalho para executar como administrador (necessário para alguns bots, ex.: SAP GUI)",
            variable=self.var_admin,
        ).pack(anchor="w")
        ttk.Checkbutton(options, text="Iniciar o worker ao terminar", variable=self.var_start).pack(anchor="w")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=10)
        self.btn_run = ttk.Button(buttons, text="Configurar", command=self._on_run)
        self.btn_run.pack(side="left")
        self.btn_copy = ttk.Button(buttons, text="Copiar log", command=self._copy_log)
        self.btn_copy.pack(side="left", padx=8)
        ttk.Button(buttons, text="Fechar", command=self.root.destroy).pack(side="right")

        self.status = ttk.Label(frame, text="Pronto para configurar.")
        self.status.pack(anchor="w")

        log_frame = ttk.LabelFrame(frame, text="Andamento e erros", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.txt = scrolledtext.ScrolledText(log_frame, height=14, wrap="word", font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("erro", foreground="#b00020")
        self.txt.tag_configure("ok", foreground="#1b7f3b")
        self.txt.configure(state="disabled")

    # --- log ---------------------------------------------------------------

    def _log(self, message: str, tag: str | None = None) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        self.txt.configure(state="normal")
        self.txt.insert("end", line + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

        try:
            SETUP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with SETUP_LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except Exception:
            pass

    def _copy_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.txt.get("1.0", "end"))
        self.status.configure(text="Log copiado para a área de transferência.")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()

                if kind == "log":
                    self._log(payload)
                elif kind == "error":
                    self._log(payload, "erro")
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass

        self.root.after(150, self._drain_events)

    # --- execução ------------------------------------------------------------

    def _validate(self) -> InstallerInput | None:
        url = self.var_url.get().strip().rstrip("/")
        login = self.var_login.get().strip()
        password = self.var_password.get()
        name = self.var_name.get().strip()
        label = self.var_label.get().strip() or name

        missing = [
            text
            for text, value in (
                ("URL da API", url),
                ("Login", login),
                ("Senha", password),
                ("Nome do runner", name),
            )
            if not value
        ]

        if missing:
            messagebox.showwarning("Campos obrigatórios", "Preencha: " + ", ".join(missing))
            return None

        if not url.lower().startswith(("http://", "https://")):
            messagebox.showwarning("URL inválida", "A URL da API deve começar com http:// ou https://")
            return None

        return InstallerInput(
            base_url=url,
            login=login,
            password=password,
            runner_name=name,
            runner_label=label,
            access_remote=self.var_remote.get(),
            run_as_admin=self.var_admin.get(),
        )

    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        data = self._validate()
        if data is None:
            return

        self.btn_run.configure(state="disabled")
        self.status.configure(text="Configurando... não feche esta janela.")
        self._log("Iniciando configuração...")

        self._worker = threading.Thread(target=self._run_flow, args=(data,), daemon=True)
        self._worker.start()

    def _run_flow(self, data: InstallerInput) -> None:
        try:
            result = run_registration_flow(
                data,
                progress_callback=lambda message: self._events.put(("log", message)),
            )
            self._events.put(("done", {"ok": True, "result": result}))
        except Exception as exc:
            self._events.put(("error", f"ERRO: {exc}"))
            self._events.put(("error", traceback.format_exc()))
            self._events.put(("done", {"ok": False, "error": str(exc)}))

    def _finish(self, payload: dict) -> None:
        self.btn_run.configure(state="normal")

        if not payload.get("ok"):
            self.status.configure(text="A configuração falhou. Veja o erro no log abaixo.")
            self._log(f"O log completo está em {SETUP_LOG_FILE}", "erro")
            messagebox.showerror(
                "Falha na configuração",
                f"{payload.get('error')}\n\nO detalhe está na área de log (use 'Copiar log' para enviar).",
            )
            return

        result = payload["result"]
        self._shortcut = result.get("desktop_shortcut")
        self.status.configure(text="Configuração concluída.")
        self._log("Configuração concluída com sucesso.", "ok")
        self._log(f"Arquivo de execução: {result.get('worker_bat')}", "ok")
        self._log(f"Atalho na área de trabalho: {self._shortcut}", "ok")

        if self.var_start.get() and self._shortcut:
            self._log("Iniciando o worker...")
            try:
                os.startfile(self._shortcut)  # type: ignore[attr-defined]
            except Exception as exc:
                self._log(f"Não foi possível iniciar automaticamente ({exc}). Abra o atalho manualmente.", "erro")

        messagebox.showinfo(
            "Concluído",
            "Máquina configurada.\n\nUse o atalho 'OrkaFlow Worker' na área de trabalho "
            "para iniciar o worker (mantenha a janela aberta).",
        )

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    SetupWindow().run()


if __name__ == "__main__":
    main()
