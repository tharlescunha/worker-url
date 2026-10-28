"""Out/2026: erro explicado pelo Maestro vira mensagem limpa, sem o JSON do protocolo no stacktrace."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.runtime import maestro_events as me  # noqa: E402
from app.runtime import task_executor as te  # noqa: E402

HELLO = 'MAESTRO {"v":1,"type":"hello","version":"1.0.0"}'
END_BUSINESS = (
    'MAESTRO {"v":1,"type":"end","status":"error","message":"Configuração: \'DB_USERNAME\' não foi informado",'
    '"category":"business","retryable":false}'
)
LOG_TRACE = 'MAESTRO {"type":"log","level":"error","message":"KeyError: x","data":{"traceback":"Traceback...\\n  KeyError: x"}}'
END_SYSTEM = 'MAESTRO {"type":"end","status":"error","message":"KeyError: x","category":"system","retryable":true}'


class CleanErrorTextTests(unittest.TestCase):
    def _state(self, *lines: str) -> me.MaestroState:
        state = me.MaestroState()
        for line in lines:
            me.handle_line(state, line)
        return state

    def test_business_error_has_message_only_and_no_protocol_json(self):
        state = self._state(HELLO, END_BUSINESS)
        stdout = "\n".join([HELLO, END_BUSINESS])
        self.assertIsNone(te._build_stacktrace("", stdout, state))
        self.assertEqual(state.end.message, "Configuração: 'DB_USERNAME' não foi informado")

    def test_unhandled_exception_uses_the_robot_traceback(self):
        state = self._state(HELLO, LOG_TRACE, END_SYSTEM)
        trace = te._build_stacktrace("", "\n".join([HELLO, LOG_TRACE, END_SYSTEM]), state)
        self.assertTrue(trace.startswith("Traceback..."))
        self.assertNotIn("MAESTRO {", trace)

    def test_plain_prints_are_kept_but_protocol_lines_are_dropped(self):
        state = self._state(HELLO, END_SYSTEM)
        trace = te._build_stacktrace("", "\n".join(["lendo planilha", HELLO, END_SYSTEM]), state)
        self.assertEqual(trace, "lendo planilha")

    def test_stderr_is_still_preferred_when_the_robot_wrote_there(self):
        state = self._state(HELLO, END_BUSINESS)
        self.assertEqual(te._build_stacktrace("boom", HELLO, state), "boom")

    def test_without_maestro_nothing_changes(self):
        self.assertEqual(te._build_stacktrace("", "MAESTRO {\"type\":\"end\"}\nx", None), "MAESTRO {\"type\":\"end\"}\nx")
        self.assertEqual(te._build_stacktrace("", "x", me.MaestroState()), "x")
