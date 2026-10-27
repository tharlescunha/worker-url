"""Testes do pacote maestro (biblioteca do robô). Rodar com:

    venv\\Scripts\\python.exe -m unittest tests.test_maestro -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maestro"))

from maestro import CredencialInvalida, Maestro, decode_line, encode_line  # noqa: E402
from maestro import protocol  # noqa: E402


def _events(buffer: io.StringIO) -> list[dict]:
    out = []
    for line in buffer.getvalue().splitlines():
        event = decode_line(line)
        if event is not None:
            out.append(event)
    return out


class _Env:
    """Define variáveis de ambiente só durante o teste."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *_):
        for key, old in self.saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class ProtocolTests(unittest.TestCase):
    def test_encode_decode_roundtrip_single_line(self):
        line = encode_line({"type": "log", "level": "info", "message": "a\nb", "data": {"x": 1}})
        self.assertTrue(line.startswith("MAESTRO {"))
        self.assertNotIn("\n", line)
        event = decode_line(line)
        self.assertEqual(event["type"], "log")
        self.assertEqual(event["data"], {"x": 1})
        self.assertEqual(event["v"], 1)

    def test_decode_rejects_text_and_bad_json(self):
        self.assertIsNone(decode_line("MAESTRO isto nao e json"))
        self.assertIsNone(decode_line("MAESTRO {\"sem_type\": 1}"))
        self.assertIsNone(decode_line("print normal do robo"))
        self.assertIsNone(decode_line("MAESTRO"))

    def test_long_line_is_cut_below_worker_limit(self):
        line = encode_line({"type": "log", "level": "info", "message": "m", "data": {"big": "x" * 10000}})
        self.assertLessEqual(len(line), 4000)
        self.assertIsNotNone(decode_line(line))

    def test_non_json_values_become_text(self):
        from datetime import datetime
        from decimal import Decimal

        line = encode_line({"type": "log", "level": "info", "message": "m", "data": {"d": datetime(2026, 1, 2), "n": Decimal("1.5")}})
        event = decode_line(line)
        self.assertEqual(event["data"]["n"], "1.5")
        self.assertIn("2026-01-02", event["data"]["d"])


class ProductionModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.task_file = self.dir / "task_42.json"
        self.result_file = self.dir / "task_42_result.json"
        self.task_file.write_text(
            json.dumps(
                {
                    "task_id": 42,
                    "automation_id": 7,
                    "correlation_id": "abc",
                    "parameters_json": {"filtros": {"filial": "010"}, "dados_acesso": {"sap_usuario": "U", "sap_senha": "S"}},
                    "runtime_parameters_json": {"lote": "A"},
                }
            ),
            encoding="utf-8",
        )
        self.env = _Env(ORKAFLOW_TASK_FILE=str(self.task_file), ORKAFLOW_RESULT_FILE=str(self.result_file), ORKAFLOW_TASK_ID="42")
        self.env.__enter__()
        self.out = io.StringIO()

    def tearDown(self):
        self.env.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_reads_params_credentials_and_task(self):
        m = Maestro(stream=self.out)
        self.assertEqual(m.task.id, 42)
        self.assertEqual(m.task.automation_id, 7)
        self.assertEqual(m.param("filtros"), {"filial": "010"})
        self.assertEqual(m.param("lote"), "A")
        self.assertEqual(m.param("nao_existe", default="x"), "x")
        self.assertNotIn("dados_acesso", m.params)
        self.assertEqual(m.credential("sap_senha"), "S")
        with self.assertRaises(Exception) as ctx:
            m.credential("outra")
        self.assertIn("outra", str(ctx.exception))
        events = _events(self.out)
        self.assertEqual(events[0]["type"], "hello")

    def test_logs_step_and_success_write_result_and_exit_0(self):
        m = Maestro(stream=self.out)
        m.info("Lendo", total=3)
        m.warning("Aviso")
        m.step(2, 5, "Enviando")
        m.next_bot("Aprovacao", parametros={"lote": "A"})
        with self.assertRaises(SystemExit) as ctx:
            m.success("Feito", result={"pedidos": 3})
        self.assertEqual(ctx.exception.code, 0)

        result = json.loads(self.result_file.read_text(encoding="utf-8"))
        self.assertEqual(result["pedidos"], 3)
        self.assertEqual(result["orkaflow"]["next_automations"], [{"parameters": {"lote": "A"}, "automation": "Aprovacao"}])

        types = [e["type"] for e in _events(self.out)]
        self.assertEqual(types, ["hello", "log", "log", "step", "end"])
        end = _events(self.out)[-1]
        self.assertEqual((end["status"], end["message"], end["next_bots"]), ("success", "Feito", ["Aprovacao"]))

    def test_fail_emits_error_end_and_exit_1(self):
        m = Maestro(stream=self.out)
        with self.assertRaises(SystemExit) as ctx:
            m.fail("Planilha não encontrada", category="business")
        self.assertEqual(ctx.exception.code, 1)
        end = _events(self.out)[-1]
        self.assertEqual((end["status"], end["category"], end["retryable"]), ("error", "business", False))
        self.assertFalse(self.result_file.exists())

    def test_with_block_turns_exception_into_error_end_and_exit_1(self):
        m = Maestro(stream=self.out)
        with self.assertRaises(SystemExit) as ctx:
            with m:
                raise ValueError("boom")
        self.assertEqual(ctx.exception.code, 1)
        events = _events(self.out)
        log = [e for e in events if e["type"] == "log"][-1]
        self.assertEqual(log["level"], "error")
        self.assertIn("boom", log["message"])
        self.assertIn("Traceback", log["data"]["traceback"])
        self.assertEqual(events[-1]["status"], "error")

    def test_with_block_without_explicit_end_is_success(self):
        m = Maestro(stream=self.out)
        with m:
            m.info("tudo certo")
        end = _events(self.out)[-1]
        self.assertEqual(end["status"], "success")

    def test_credential_invalid_prints_alert_line_and_exits_3(self):
        m = Maestro(stream=self.out)
        with self.assertRaises(SystemExit) as ctx:
            with m:
                raise CredencialInvalida("sap_senha", motivo="senha_expirada", mensagem="SAP: venceu", sistema="SAP")
        self.assertEqual(ctx.exception.code, 3)
        alert_lines = [l for l in self.out.getvalue().splitlines() if l.startswith(protocol.CREDENTIAL_ALERT_MARKER)]
        self.assertEqual(len(alert_lines), 1)
        payload = json.loads(alert_lines[0][len(protocol.CREDENTIAL_ALERT_MARKER) + 1 :])
        self.assertEqual(payload, {"sistema": "SAP", "chave": "sap_senha", "motivo": "senha_expirada", "mensagem": "SAP: venceu"})

    def test_manual_sys_exit_inside_with_is_respected(self):
        m = Maestro(stream=self.out)
        with self.assertRaises(SystemExit) as ctx:
            with m:
                sys.exit(5)
        self.assertEqual(ctx.exception.code, 5)
        self.assertEqual(_events(self.out)[-1]["status"], "error")

    def test_closed_stdout_never_breaks_the_bot(self):
        class Broken(io.StringIO):
            def write(self, *_):
                raise OSError("fechado")

        m = Maestro(stream=Broken())
        m.info("x")  # não levanta


class LocalModeTests(unittest.TestCase):
    def test_reads_task_json_next_to_main_and_prints_readable_lines(self):
        with TemporaryDirectory() as tmp, _Env(ORKAFLOW_TASK_FILE=None, ORKAFLOW_RESULT_FILE=None, ORKAFLOW_TASK_ID=None):
            base = Path(tmp)
            (base / "task.json").write_text(
                json.dumps({"parameters_json": {"x": 1, "dados_acesso": {"credential_id": 9, "itens": {"senha": "local"}}}, "env": {"SAP_URL": "u"}}),
                encoding="utf-8",
            )
            out = io.StringIO()
            m = Maestro(producao=False, task_file=base / "task.json", result_file=base / "resultado.json", stream=out)
            self.assertEqual(m.param("x"), 1)
            self.assertEqual(m.credential("senha"), "local")
            self.assertEqual(m.env("SAP_URL"), "u")
            m.info("ola", k=2)
            with self.assertRaises(SystemExit):
                m.success("fim", result={"a": 1})
            text = out.getvalue()
            self.assertIn("[INFO] ola", text)
            self.assertIn("[END SUCCESS] fim", text)
            self.assertNotIn("MAESTRO {", text)
            self.assertEqual(json.loads((base / "resultado.json").read_text(encoding="utf-8")), {"a": 1})

    def test_init_command_creates_template(self):
        from maestro.__main__ import main

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "task.json"
            self.assertEqual(main(["init", str(target)]), 0)
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("dados_acesso", data["parameters_json"])
            self.assertEqual(main(["init", str(target)]), 1)


if __name__ == "__main__":
    unittest.main()
