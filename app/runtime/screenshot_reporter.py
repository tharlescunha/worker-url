from __future__ import annotations

import base64
import threading
import time
from io import BytesIO

import mss
from PIL import Image

from app.core.constants import SCREENSHOT_INTERVAL_SECONDS
from app.runtime.task_client import TaskApiClient


class ScreenshotReporter:
    """
    Envia prints da tela durante a execução da task.

    Fluxo:
    - envia um print ao iniciar
    - envia a cada 10 segundos enquanto a task roda
    - envia um print final ao terminar
    """

    def __init__(
        self,
        api: TaskApiClient,
        interval_seconds: int = SCREENSHOT_INTERVAL_SECONDS,
        logger=None,
    ) -> None:
        self.api = api
        self.interval_seconds = max(1, int(interval_seconds))
        self.logger = logger

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

        try:
            self._send_screenshot()
        except Exception as exc:
            if self.logger:
                self.logger.warning("Falha ao enviar screenshot inicial: %s", exc)

        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="screenshot-reporter",
        )
        self._thread.start()

        if self.logger:
            self.logger.info("ScreenshotReporter iniciado com intervalo de %s segundos.", self.interval_seconds)

    def stop(self, send_final: bool = True) -> None:
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        if send_final:
            try:
                self._send_screenshot()
                if self.logger:
                    self.logger.info("Screenshot final enviado.")
            except Exception as exc:
                if self.logger:
                    self.logger.warning("Falha ao enviar screenshot final: %s", exc)

        if self.logger:
            self.logger.info("ScreenshotReporter finalizado.")

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self._send_screenshot()
            except Exception as exc:
                if self.logger:
                    self.logger.warning("Erro ao capturar/enviar screenshot: %s", exc)

    def _send_screenshot(self) -> None:
        image_base64 = self._capture_screen_base64()

        self.api.send_screenshot(
            image_base64=image_base64,
            content_type="image/png",
        )

    def _capture_screen_base64(self) -> str:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)

            img = Image.frombytes(
                "RGB",
                screenshot.size,
                screenshot.rgb,
            )

            buffer = BytesIO()
            img.save(buffer, format="PNG")

            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        