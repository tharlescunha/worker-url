"""
Envio do worker.log para a tela de Logs e mascaramento de credenciais em URL.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.exceptions import ApiRequestError, ForbiddenError
from app.core.log_masking import UrlCredentialsFilter, mask_url_credentials
from app.runtime import log_shipper
from app.runtime.log_shipper import LogShipperThread, RemoteLogHandler


class FakeClient:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def post(self, path, payload, **kwargs):
        self.calls.append((path, payload))
        if self.error:
            raise self.error
        return {"accepted": len(payload["lines"])}


def make_logger(name: str) -> tuple[logging.Logger, RemoteLogHandler]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RemoteLogHandler(max_lines=10)
    handler.addFilter(UrlCredentialsFilter())
    logger.addHandler(handler)
    return logger, handler


def make_shipper(handler, logger, client) -> LogShipperThread:
    return LogShipperThread(handler, "http://x", "uuid-1", "tok", logger, client=client)


class MaskingTests(unittest.TestCase):
    def test_masks_user_and_token_in_urls(self):
        text = "Falha ao clonar https://org:SEGREDO123@dev.azure.com/org/_git/repo: fatal"
        masked = mask_url_credentials(text)
        self.assertNotIn("SEGREDO123", masked)
        self.assertIn("https://***@dev.azure.com/org/_git/repo", masked)
        self.assertEqual(mask_url_credentials("http://10.0.0.1:8008/api e user@mail.com"),
                         "http://10.0.0.1:8008/api e user@mail.com")

    def test_filter_masks_formatted_args_and_tracebacks(self):
        logger, handler = make_logger("test.mask")
        logger.info("clone %s", "https://u:pat@github.com/x.git")
        try:
            raise RuntimeError("https://u:pat2@github.com/y.git")
        except RuntimeError:
            logger.exception("falhou")

        messages = [line["message"] for line in handler.take(10)]
        self.assertEqual(messages[0], "clone https://***@github.com/x.git")
        self.assertNotIn("pat2", messages[1])


class ShipperTests(unittest.TestCase):
    def test_sends_lines_with_runner_credentials(self):
        logger, handler = make_logger("test.ship")
        logger.info("[BOT][task_id=7] ola")
        client = FakeClient()

        wait = make_shipper(handler, logger, client).ship_once()

        self.assertEqual(wait, log_shipper.SHIP_INTERVAL_SECONDS)
        path, payload = client.calls[0]
        self.assertEqual(path, log_shipper.RUNNER_LOGS_PATH)
        self.assertEqual((payload["uuid"], payload["token"]), ("uuid-1", "tok"))
        self.assertEqual(payload["lines"][0]["message"], "[BOT][task_id=7] ola")
        self.assertEqual(payload["lines"][0]["level"], "INFO")
        self.assertEqual(handler.pending(), 0)

    def test_failure_keeps_lines_and_backs_off(self):
        logger, handler = make_logger("test.fail")
        logger.info("a")
        shipper = make_shipper(handler, logger, FakeClient(ApiRequestError("HTTP 503: fora")))

        wait = shipper.ship_once()

        self.assertGreater(wait, log_shipper.SHIP_INTERVAL_SECONDS)
        lines = [line["message"] for line in handler.take(10)]
        self.assertEqual(lines[0], "a", "linha que falhou volta para a frente da fila")

    def test_forbidden_does_not_raise(self):
        logger, handler = make_logger("test.forbidden")
        logger.info("a")
        shipper = make_shipper(handler, logger, FakeClient(ForbiddenError("HTTP 403: Runner removido")))
        self.assertGreater(shipper.ship_once(), log_shipper.SHIP_INTERVAL_SECONDS)

    def test_old_backend_404_pauses_and_drops_batch(self):
        logger, handler = make_logger("test.404")
        logger.info("a")
        shipper = make_shipper(handler, logger, FakeClient(ApiRequestError("HTTP 404: Not Found")))

        self.assertEqual(shipper.ship_once(), log_shipper.DISABLED_AFTER_404_SECONDS)
        remaining = [line["message"] for line in handler.take(10)]
        self.assertNotIn("a", remaining)

    def test_queue_is_bounded_and_drops_oldest(self):
        logger, handler = make_logger("test.bound")
        for i in range(25):
            logger.info("linha %s", i)
        messages = [line["message"] for line in handler.take(100)]
        self.assertEqual(len(messages), 10)
        self.assertEqual(messages[-1], "linha 24")

    def test_big_backlog_is_sent_in_batches(self):
        logger = logging.getLogger("test.batch")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = RemoteLogHandler(max_lines=2000)
        logger.addHandler(handler)
        for i in range(1200):
            logger.info("l%s", i)
        client = FakeClient()

        make_shipper(handler, logger, client).ship_once()

        self.assertEqual([len(p["lines"]) for _, p in client.calls], [500, 500, 200])


if __name__ == "__main__":
    unittest.main()
