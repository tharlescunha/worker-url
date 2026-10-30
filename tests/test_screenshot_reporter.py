import threading
import time
import unittest
from unittest import mock

from app.runtime import screenshot_reporter as sr


class _Api:
    def __init__(self):
        self.sent = 0

    def send_screenshot(self, **_):
        self.sent += 1


def _reporter(api, capture):
    rep = sr.ScreenshotReporter(api=api, interval_seconds=1)
    rep._capture_screen_base64 = capture
    return rep


class ScreenshotReporterTests(unittest.TestCase):
    def test_start_nao_espera_captura_travada(self):
        release = threading.Event()
        rep = _reporter(_Api(), lambda: (release.wait(30), "x")[1])

        began = time.monotonic()
        rep.start()
        self.assertLess(time.monotonic() - began, 1)

        release.set()
        rep._stop_event.set()

    def test_primeiro_print_sai_pela_thread_logo_no_inicio(self):
        api = _Api()
        rep = _reporter(api, lambda: "x")
        rep.start()
        time.sleep(0.3)
        rep.stop(send_final=False)
        self.assertGreaterEqual(api.sent, 1)

    def test_stop_nao_fica_preso_em_print_final_travado(self):
        release = threading.Event()
        rep = _reporter(_Api(), lambda: (release.wait(30), "x")[1])

        with mock.patch.object(sr, "FINAL_SCREENSHOT_DEADLINE_SECONDS", 0.3):
            began = time.monotonic()
            rep.stop(send_final=True)
        self.assertLess(time.monotonic() - began, 2)
        release.set()

    def test_release_para_o_reporter_fora_do_lock(self):
        livre_durante_stop = []

        class _Fake:
            def stop(self, send_final=True):
                ok = sr._shared_lock.acquire(blocking=False)
                livre_durante_stop.append(ok)
                if ok:
                    sr._shared_lock.release()

        with mock.patch.object(sr, "_shared_reporter", _Fake()), mock.patch.object(sr, "_shared_refs", 1):
            sr.release_screenshots()

        self.assertEqual(livre_durante_stop, [True])


if __name__ == "__main__":
    unittest.main()
