"""Limpeza de processos que sobram depois de falha (SAP etc.)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime import cleanup

LOGGER = logging.getLogger("test-cleanup")


class RulesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._original = cleanup.CLEANUP_RULES_FILE
        cleanup.CLEANUP_RULES_FILE = Path(self._tmp.name) / "cleanup_rules.json"
        self.addCleanup(lambda: setattr(cleanup, "CLEANUP_RULES_FILE", self._original))

    def test_default_file_is_created_and_sap_rule_matches_output(self):
        rules = cleanup.load_rules(LOGGER)
        self.assertTrue(cleanup.CLEANUP_RULES_FILE.exists())

        sap = next(rule for rule in rules if rule.name == "SAP")
        self.assertTrue(sap.matches("BOT_ETL_Estoque_PO", "RuntimeError: Falha no login SAP: senha incorreta"))
        self.assertFalse(sap.matches("BOT_ETL_Estoque_PO", "erro qualquer de banco de dados"))

    def test_rule_without_processes_never_matches(self):
        sigam = next(rule for rule in cleanup.load_rules(LOGGER) if rule.name == "SIGAM")
        self.assertFalse(sigam.matches("BOT", "Falha no SIGAM"))

    def test_match_by_bot_name_and_disabled_rule(self):
        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps({"rules": [
            {"name": "X", "enabled": True, "match_bot_names": ["estoque"], "processes": ["a.exe"]},
            {"name": "Y", "enabled": False, "match_output_regex": "erro", "processes": ["b.exe"]},
        ]}), encoding="utf-8")

        x, y = cleanup.load_rules(LOGGER)
        self.assertTrue(x.matches("BOT_ETL_Estoque_PO", ""))
        self.assertFalse(y.matches("qualquer", "erro"))

    def test_invalid_file_falls_back_to_defaults(self):
        cleanup.CLEANUP_RULES_FILE.write_text("{ isto não é json", encoding="utf-8")
        names = [rule.name for rule in cleanup.load_rules(LOGGER)]
        self.assertIn("SAP", names)


class KillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._original = cleanup.CLEANUP_RULES_FILE
        cleanup.CLEANUP_RULES_FILE = Path(self._tmp.name) / "cleanup_rules.json"
        self.addCleanup(lambda: setattr(cleanup, "CLEANUP_RULES_FILE", self._original))

        # processo "falso" com nome próprio: cópia do ping.exe que fica rodando
        self.fake = Path(self._tmp.name) / "fakesaptest.exe"
        shutil.copy(Path(r"C:\Windows\System32\PING.EXE"), self.fake)
        self.proc = subprocess.Popen(
            [str(self.fake), "-n", "120", "127.0.0.1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        def _stop_fake():
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=10)  # libera o .exe antes de apagar a pasta temporária

        self.addCleanup(_stop_fake)
        time.sleep(0.5)

        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps({"rules": [
            {"name": "FAKE", "enabled": True, "match_output_regex": r"\bSAP\b",
             "processes": ["fakesaptest.exe"], "before_foreground": True},
        ]}), encoding="utf-8")

    def test_failure_with_sap_in_output_kills_leftover_process(self):
        self.assertIsNone(self.proc.poll(), "o processo falso precisa estar rodando")

        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="Falha no login SAP", logger=LOGGER
        )

        self.assertTrue(messages and "fakesaptest.exe" in messages[0])
        self.proc.wait(timeout=10)
        self.assertIsNotNone(self.proc.poll())

    def test_failure_unrelated_to_sap_leaves_process_alone(self):
        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="erro de planilha", logger=LOGGER
        )
        self.assertEqual(messages, [])
        self.assertIsNone(self.proc.poll())

    def test_does_not_kill_while_another_foreground_task_runs(self):
        cleanup.mark_foreground_started(500)
        self.addCleanup(lambda: cleanup.mark_foreground_finished(500))

        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="Falha no login SAP", logger=LOGGER
        )
        self.assertEqual(messages, [])
        self.assertIsNone(self.proc.poll(), "não pode derrubar o SAP de outra task com foco")

    def test_cleanup_before_foreground_only_for_flagged_rules(self):
        messages = cleanup.cleanup_before_foreground(task_id=2, logger=LOGGER)
        self.assertTrue(messages)
        self.proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
