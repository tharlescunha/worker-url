"""Limpeza de processos que sobram depois de falha (SAP, CIGAM, Citrix...)."""

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


class _RulesFileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._original = cleanup.CLEANUP_RULES_FILE
        cleanup.CLEANUP_RULES_FILE = Path(self._tmp.name) / "cleanup_rules.json"
        self.addCleanup(lambda: setattr(cleanup, "CLEANUP_RULES_FILE", self._original))


class RulesTests(_RulesFileCase):
    def test_defaults_cover_sap_cigam_and_citrix_with_wildcards(self):
        rules = {rule.name: rule for rule in cleanup.load_rules(LOGGER)}

        self.assertTrue(cleanup.CLEANUP_RULES_FILE.exists())
        self.assertEqual(rules["SAP"].processes, ["sap*"])
        self.assertEqual(rules["CIGAM"].processes, ["cigam*"])
        self.assertIn("citrix*", rules["Citrix"].processes)
        self.assertTrue(all(rule.before_foreground and rule.on_foreground_failure for rule in rules.values()))

    def test_any_foreground_failure_triggers_cleanup_but_background_needs_a_match(self):
        sap = next(rule for rule in cleanup.load_rules(LOGGER) if rule.name == "SAP")

        self.assertTrue(sap.matches_failure("BOT_X", "erro qualquer", is_foreground=True))
        self.assertFalse(sap.matches_failure("BOT_X", "erro qualquer", is_foreground=False))
        self.assertTrue(sap.matches_failure("BOT_X", "Falha no login SAP", is_foreground=False))

    def test_match_by_bot_name_and_disabled_rule(self):
        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps({"version": cleanup.RULES_VERSION, "rules": [
            {"name": "X", "enabled": True, "match_bot_names": ["estoque"], "processes": ["a.exe"]},
            {"name": "Y", "enabled": False, "on_foreground_failure": True, "processes": ["b.exe"]},
            {"name": "Z", "enabled": True, "on_foreground_failure": True, "processes": []},
        ]}), encoding="utf-8")

        x, y, z = cleanup.load_rules(LOGGER)
        self.assertTrue(x.matches_failure("BOT_ETL_Estoque_PO", "", False))
        self.assertFalse(y.matches_failure("qualquer", "", True))
        self.assertFalse(z.matches_failure("qualquer", "", True), "regra sem processos não faz nada")

    def test_invalid_file_falls_back_to_defaults(self):
        cleanup.CLEANUP_RULES_FILE.write_text("{ isto não é json", encoding="utf-8")
        self.assertIn("SAP", [rule.name for rule in cleanup.load_rules(LOGGER)])

    def test_old_version_is_backed_up_and_replaced_with_new_defaults(self):
        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps({"rules": [
            {"name": "SAP", "processes": ["saplogon.exe"]},
        ]}), encoding="utf-8")

        rules = {rule.name: rule for rule in cleanup.load_rules(LOGGER)}

        self.assertEqual(rules["SAP"].processes, ["sap*"])
        backup = cleanup.CLEANUP_RULES_FILE.with_suffix(".json.bak")
        self.assertTrue(backup.exists())
        self.assertIn("saplogon.exe", backup.read_text(encoding="utf-8"))

    def test_user_edits_on_current_version_are_kept(self):
        cleanup.load_rules(LOGGER)
        data = json.loads(cleanup.CLEANUP_RULES_FILE.read_text(encoding="utf-8"))
        data["rules"][0]["processes"] = ["meusap.exe"]
        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps(data), encoding="utf-8")

        rules = cleanup.load_rules(LOGGER)
        self.assertEqual(rules[0].processes, ["meusap.exe"])


class KillTests(_RulesFileCase):
    def setUp(self):
        super().setUp()

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

        cleanup.CLEANUP_RULES_FILE.write_text(json.dumps({"version": cleanup.RULES_VERSION, "rules": [
            {"name": "FAKE", "enabled": True, "processes": ["fakesap*"],
             "before_foreground": True, "on_foreground_failure": True,
             "match_output_regex": r"\bSAP\b"},
        ]}), encoding="utf-8")

    def test_foreground_failure_kills_by_wildcard(self):
        self.assertIsNone(self.proc.poll(), "o processo falso precisa estar rodando")

        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="", logger=LOGGER, is_foreground=True
        )

        self.assertTrue(messages and "fakesap*" in messages[0])
        self.proc.wait(timeout=10)
        self.assertIsNotNone(self.proc.poll())

    def test_background_failure_without_match_leaves_process_alone(self):
        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="erro de planilha", logger=LOGGER, is_foreground=False
        )
        self.assertEqual(messages, [])
        self.assertIsNone(self.proc.poll())

    def test_background_failure_mentioning_sap_kills_it(self):
        messages = cleanup.cleanup_after_failure(
            task_id=1, bot_name="BOT", output_text="Falha no login SAP", logger=LOGGER, is_foreground=False
        )
        self.assertTrue(messages)
        self.proc.wait(timeout=10)

    def test_does_not_kill_while_another_foreground_task_runs(self):
        cleanup.mark_foreground_started(500)
        self.addCleanup(lambda: cleanup.mark_foreground_finished(500))

        self.assertEqual(
            cleanup.cleanup_after_failure(
                task_id=1, bot_name="BOT", output_text="Falha SAP", logger=LOGGER, is_foreground=True
            ),
            [],
        )
        self.assertEqual(cleanup.cleanup_before_foreground(task_id=1, logger=LOGGER), [])
        self.assertIsNone(self.proc.poll(), "não pode derrubar a tela de outra task com foco")

    def test_cleanup_before_foreground_kills_leftovers(self):
        messages = cleanup.cleanup_before_foreground(task_id=2, logger=LOGGER)
        self.assertTrue(messages)
        self.proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
