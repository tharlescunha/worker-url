"""Lado do worker: linhas MAESTRO no stdout, instalação no venv. Rodar com:

    venv\\Scripts\\python.exe -m unittest tests.test_maestro_worker -v
"""
from __future__ import annotations

import json
import logging
import subprocess as sp
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime import maestro_events as me  # noqa: E402
from app.runtime import task_executor as te  # noqa: E402


class _Api:
    def __init__(self):
        self.logs: list[dict] = []

    def send_log(self, **entry):  # usado pelo AsyncTaskLogSender
        self.logs.append(entry)
        return {}

    def check_task_status(self, task_id):
        return {"status": "running"}


def _run_bot(code: str, state: me.MaestroState, api: _Api):
    process = sp.Popen(
        [sys.executable, "-u", "-c", code], stdout=sp.PIPE, stderr=sp.PIPE, text=True, encoding="utf-8"
    )
    return te._consume_process_output_live(
        process=process,
        timeout_seconds=30,
        masker=te.SecretMasker(["s3gredo!"]),
        logger=logging.getLogger("t"),
        task_id=1,
        api=api,
        maestro_state=state,
    )


class HandleLineTests(unittest.TestCase):
    def test_plain_text_and_broken_lines_are_not_events(self):
        state = me.MaestroState()
        self.assertIsNone(me.handle_line(state, "print comum"))
        self.assertIsNone(me.handle_line(state, "MAESTRO nao e json"))
        self.assertIsNone(me.handle_line(state, 'MAESTRO {"type":"desconhecido"}'))
        self.assertIsNone(me.handle_line(None, 'MAESTRO {"type":"log","level":"info","message":"x"}'))
        self.assertFalse(state.used)
        self.assertEqual(state.invalid_lines, 2)

    def test_log_levels_and_data(self):
        state = me.MaestroState()
        level, text, send = me.handle_line(state, 'MAESTRO {"v":1,"type":"log","level":"warning","message":"Linha sem CNPJ","data":{"linha":37,"ids":[1,2]}}')
        self.assertEqual((level, send), ("warning", True))
        self.assertEqual(text, "Linha sem CNPJ | linha=37 ids=[1, 2]")
        level, _, _ = me.handle_line(state, 'MAESTRO {"type":"log","level":"invalido","message":"m"}')
        self.assertEqual(level, "info")

    def test_step_screenshot_heartbeat_end(self):
        state = me.MaestroState()
        _, text, _ = me.handle_line(state, 'MAESTRO {"type":"step","current":3,"total":7,"text":"Enviando"}')
        self.assertEqual(text, "Etapa 3 de 7: Enviando")
        self.assertEqual((state.last_step.current, state.last_step.total), (3, 7))

        me.handle_line(state, 'MAESTRO {"type":"screenshot","path":"C:/x/a.png","description":"Tela"}')
        self.assertEqual(state.screenshots[-1]["path"], "C:/x/a.png")

        _, _, send = me.handle_line(state, 'MAESTRO {"type":"heartbeat"}')
        self.assertFalse(send)

        level, text, _ = me.handle_line(state, 'MAESTRO {"type":"end","status":"error","message":"Planilha sumiu","category":"business","retryable":false,"next_automations":[]}')
        self.assertEqual(level, "error")
        self.assertIn("Planilha sumiu", text)
        self.assertEqual((state.end.status, state.end.category, state.end.retryable), ("error", "business", False))
        self.assertEqual(me.error_type_for(state.end, "bot_exit_code"), "bot_business_error")
        me.handle_line(state, 'MAESTRO {"type":"end","status":"success","message":"ok","items_processed":42,"items_failed":2}')
        self.assertEqual((state.end.items_processed, state.end.items_failed), (42, 2))
        self.assertEqual(te._maestro_items(state, "items_processed", 0), 42)
        self.assertEqual(te._maestro_items(me.MaestroState(), "items_failed", 1), 1)

    def test_hello_records_version(self):
        state = me.MaestroState()
        _, text, _ = me.handle_line(state, 'MAESTRO {"type":"hello","version":"1.0.0"}')
        self.assertEqual(state.version, "1.0.0")
        self.assertIn("1.0.0", text)


class LiveOutputTests(unittest.TestCase):
    def test_maestro_lines_change_level_and_message_but_plain_lines_pass_through(self):
        api = _Api()
        state = me.MaestroState()
        code = (
            "print('comecando', flush=True);"
            "print('MAESTRO {\"v\":1,\"type\":\"log\",\"level\":\"error\",\"message\":\"deu ruim\"}', flush=True);"
            "print('MAESTRO {\"type\":\"heartbeat\"}', flush=True);"
            "print('MAESTRO {\"type\":\"end\",\"status\":\"success\",\"message\":\"Pronto: 3 itens\"}', flush=True)"
        )
        stdout, _ = _run_bot(code, state, api)
        self.assertIn("comecando", stdout)
        self.assertEqual(state.end.status, "success")
        self.assertEqual(state.end.message, "Pronto: 3 itens")
        messages = [(e.get("level"), e.get("message")) for e in api.logs]
        self.assertIn(("info", "comecando"), messages)
        self.assertIn(("error", "deu ruim"), messages)
        self.assertFalse(any("heartbeat" in (m or "") for _, m in messages))

    def test_secret_inside_maestro_line_is_masked_before_parsing(self):
        api = _Api()
        state = me.MaestroState()
        code = "print('MAESTRO {\"type\":\"log\",\"level\":\"info\",\"message\":\"senha s3gredo!\"}', flush=True)"
        _run_bot(code, state, api)
        self.assertTrue(any("senha ***" in (e.get("message") or "") for e in api.logs))
        self.assertFalse(any("s3gredo" in (e.get("message") or "") for e in api.logs))

    def test_without_maestro_state_nothing_changes(self):
        api = _Api()
        code = "print('MAESTRO {\"type\":\"end\",\"status\":\"error\",\"message\":\"x\"}', flush=True)"
        process = sp.Popen([sys.executable, "-u", "-c", code], stdout=sp.PIPE, stderr=sp.PIPE, text=True, encoding="utf-8")
        te._consume_process_output_live(process=process, timeout_seconds=30, logger=logging.getLogger("t"), task_id=1, api=api)
        # Worker chamado do jeito antigo: a linha vai para o log como texto, nível info.
        self.assertEqual([(e.get("level")) for e in api.logs], ["info"])
        self.assertTrue(api.logs[0]["message"].startswith("MAESTRO {"))


class ScreenshotAndFinalMessageTests(unittest.TestCase):
    def test_final_message_and_screenshot_helpers(self):
        state = me.MaestroState()
        self.assertEqual(te._maestro_final_message(state, "padrao"), "padrao")
        state.end = me.MaestroEnd(status="success", message="Feito 10")
        self.assertEqual(te._maestro_final_message(state, "padrao"), "Feito 10")
        state.end = me.MaestroEnd(status="error", message="x")
        self.assertEqual(te._maestro_final_message(state, "padrao"), "padrao")

        logger = logging.getLogger("t")
        self.assertIsNone(te._load_maestro_screenshot_base64(me.MaestroState(), logger))
        state.screenshots = [{"path": "C:/nao/existe.png"}]
        self.assertIsNone(te._load_maestro_screenshot_base64(state, logger))

        with TemporaryDirectory() as tmp:
            from PIL import Image

            path = Path(tmp) / "shot.png"
            Image.new("RGB", (2000, 500), "red").save(path)
            state.screenshots = [{"path": str(path)}]
            encoded = te._load_maestro_screenshot_base64(state, logger)
            self.assertIsNotNone(encoded)
            import base64
            from io import BytesIO

            img = Image.open(BytesIO(base64.b64decode(encoded)))
            self.assertEqual(img.width, 1600)
            te._cleanup_maestro_screenshots(state, logger)
            self.assertFalse(path.exists())


class VenvInstallTests(unittest.TestCase):
    def test_copies_package_into_site_packages_once_per_version(self):
        from app.sync import maestro_install as mi

        with TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            (venv / "Lib" / "site-packages").mkdir(parents=True)
            messages = []
            self.assertTrue(mi.ensure_maestro_in_venv(venv, progress_callback=messages.append, bot_label="b"))
            pkg = venv / "Lib" / "site-packages" / "maestro"
            self.assertTrue((pkg / "__init__.py").exists())
            self.assertTrue((pkg / "core.py").exists())
            self.assertEqual(mi.installed_version(venv), mi.maestro_version())
            # Segunda vez: já está, não copia de novo.
            self.assertFalse(mi.ensure_maestro_in_venv(venv, progress_callback=messages.append))
            self.assertEqual(len(messages), 1)

            # O pacote copiado importa e funciona com o python do venv (simulado via sys.path).
            out = sp.run(
                [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, r'{pkg.parent}'); import maestro; print(maestro.__version__)"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(out.stdout.strip(), mi.maestro_version())

    def test_missing_site_packages_is_not_an_error(self):
        from app.sync import maestro_install as mi

        with TemporaryDirectory() as tmp:
            self.assertFalse(mi.ensure_maestro_in_venv(Path(tmp) / "nao-existe"))


if __name__ == "__main__":
    unittest.main()
