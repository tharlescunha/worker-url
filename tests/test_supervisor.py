"""Testa o supervisor: reinício por travamento (pulso parado) e saída do filho."""

from __future__ import annotations

import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.runtime.supervisor as supervisor


class SupervisorTests(unittest.TestCase):
    def _run_with_child(self, code: str, **limits):
        real_popen = subprocess.Popen

        def fake_popen(args, **kwargs):
            return real_popen([sys.executable, "-c", code], **kwargs)

        # Só o Popen usado pelo supervisor é trocado; subprocess.run (taskkill) segue real.
        shim = types.SimpleNamespace(
            Popen=fake_popen,
            run=subprocess.run,
            TimeoutExpired=subprocess.TimeoutExpired,
        )

        with mock.patch.object(supervisor, "subprocess", shim), \
                mock.patch.object(supervisor, "CHECK_INTERVAL_SECONDS", 0.2), \
                mock.patch.object(supervisor, "SUPERVISOR_STARTUP_GRACE_SECONDS", limits.get("grace", 1)), \
                mock.patch.object(supervisor, "SUPERVISOR_STALL_LIMIT_SECONDS", limits.get("stall", 1)), \
                mock.patch.object(supervisor, "CRASH_LOOP_EXTRA_WAIT_SECONDS", 0):
            return supervisor.run()

    def test_stalled_child_is_killed_and_reports_stall(self):
        started = time.time()
        code = self._run_with_child("import time; time.sleep(60)")
        self.assertEqual(code, supervisor.EXIT_STALLED)
        self.assertLess(time.time() - started, 15, "deve matar o filho travado rapidamente")

    def test_child_exit_returns_zero_for_restart(self):
        code = self._run_with_child("import sys; sys.exit(7)", grace=60, stall=60)
        self.assertEqual(code, 0)

    def test_user_interrupt_code_is_propagated(self):
        code = self._run_with_child("import sys; sys.exit(130)", grace=60, stall=60)
        self.assertEqual(code, supervisor.EXIT_INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
