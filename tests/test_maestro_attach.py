"""Anexos (maestro.attach) do lado do pacote e do worker."""
from __future__ import annotations

import io
import json
import logging
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "maestro"))
sys.path.insert(0, str(ROOT))

from app.runtime import maestro_events as me  # noqa: E402
from app.runtime import task_executor as te  # noqa: E402
from maestro import Maestro, decode_line  # noqa: E402


class PackageAttachTests(unittest.TestCase):
    def test_attach_emits_event_and_refuses_missing_or_big_files(self):
        with TemporaryDirectory() as tmp:
            out = io.StringIO()
            m = Maestro(producao=True, task_file=Path(tmp) / "nao.json", stream=out)
            f = Path(tmp) / "saida.xlsx"
            f.write_bytes(b"dados")
            self.assertTrue(m.attach(f, "Relatório"))
            self.assertFalse(m.attach(Path(tmp) / "nao_existe.txt"))
            big = Path(tmp) / "grande.bin"
            big.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
            self.assertFalse(m.attach(big))
            events = [decode_line(l) for l in out.getvalue().splitlines() if decode_line(l)]
            attach = [e for e in events if e["type"] == "attach"]
            self.assertEqual(len(attach), 1)
            self.assertEqual((attach[0]["name"], attach[0]["size"], attach[0]["description"]), ("saida.xlsx", 5, "Relatório"))
            warnings = [e for e in events if e["type"] == "log" and e["level"] == "warning" and "Anexo ignorado" in e["message"]]
            self.assertEqual(len(warnings), 2)


class _Api:
    def __init__(self):
        self.sent = []
        self.event = threading.Event()

    def send_attachment(self, task_id, **kw):
        self.sent.append((task_id, kw))
        self.event.set()
        return {}


class WorkerAttachTests(unittest.TestCase):
    def test_worker_reads_file_at_once_and_uploads_in_thread(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "rel.csv"
            f.write_text("a;b\n1;2\n", encoding="utf-8")
            state = me.MaestroState()
            line = "MAESTRO " + json.dumps({"type": "attach", "path": str(f), "name": "rel.csv", "size": 8, "description": "csv"})
            level, text, send = me.handle_line(state, line)
            self.assertEqual((level, send), ("info", True))
            self.assertIn("rel.csv", text)

            api = _Api()
            te._send_maestro_attachments_if_new(state, api, 7, logging.getLogger("t"))
            f.unlink()  # o robô pode apagar logo depois: o worker já leu
            self.assertTrue(api.event.wait(5))
            task_id, kw = api.sent[0]
            # O tipo vem do registro do Windows (csv pode virar vnd.ms-excel): só precisa existir.
            self.assertEqual((task_id, kw["filename"]), (7, "rel.csv"))
            self.assertTrue(kw["content_type"])
            import base64

            self.assertEqual(base64.b64decode(kw["content_base64"]), b"a;b\n1;2\n")
            # Segunda chamada sem anexos novos não reenvia.
            te._send_maestro_attachments_if_new(state, api, 7, logging.getLogger("t"))
            time.sleep(0.2)
            self.assertEqual(len(api.sent), 1)

    def test_limit_of_ten_attachments(self):
        state = me.MaestroState()
        for i in range(11):
            level, _, _ = me.handle_line(state, 'MAESTRO {"type":"attach","path":"C:/x/%d.txt","name":"%d.txt"}' % (i, i))
        self.assertEqual(len(state.attachments), 10)
        self.assertEqual(level, "warning")
