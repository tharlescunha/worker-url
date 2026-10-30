from __future__ import annotations

import base64
import threading
import time
from io import BytesIO

import mss
from PIL import Image

from app.core.constants import SCREENSHOT_INTERVAL_SECONDS
from app.runtime.task_client import TaskApiClient

FINAL_SCREENSHOT_DEADLINE_SECONDS = 10


class ScreenshotReporter:
    """
    Envia prints da tela durante a execução da task.

    Fluxo:
    - envia um print ao iniciar (pela thread do reporter, sem prender quem iniciou)
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
        # Não captura nem envia nada aqui: quem chama (início da task) não pode ficar preso
        # numa tela travada ou numa rede lenta. O primeiro print sai pela própria thread.
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()

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
            # Em thread própria e com prazo: tela bloqueada não segura o fim da task.
            sender = threading.Thread(target=self._send_final, daemon=True, name="screenshot-final")
            sender.start()
            sender.join(timeout=FINAL_SCREENSHOT_DEADLINE_SECONDS)
            if sender.is_alive() and self.logger:
                self.logger.warning(
                    "Screenshot final não terminou em %ss; seguindo sem ele.",
                    FINAL_SCREENSHOT_DEADLINE_SECONDS,
                )

        if self.logger:
            self.logger.info("ScreenshotReporter finalizado.")

    def _send_final(self) -> None:
        try:
            self._send_screenshot()
            if self.logger:
                self.logger.info("Screenshot final enviado.")
        except Exception as exc:
            if self.logger:
                self.logger.warning("Falha ao enviar screenshot final: %s", exc)

    def _run(self) -> None:
        # Primeiro print logo ao iniciar, depois a cada intervalo.
        wait_seconds = 0
        while not self._stop_event.wait(wait_seconds):
            wait_seconds = self.interval_seconds
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
        

# --- Uso compartilhado entre várias tasks simultâneas -------------------------
# O print é da tela da máquina (não da task), então um único reporter atende
# todas as tasks em andamento: inicia na primeira e finaliza quando a última sai.

_shared_lock = threading.Lock()
_shared_reporter: ScreenshotReporter | None = None
_shared_refs = 0


def acquire_screenshots(api: TaskApiClient, logger=None) -> None:
    global _shared_reporter, _shared_refs

    with _shared_lock:
        _shared_refs += 1

        if _shared_refs == 1:
            _shared_reporter = ScreenshotReporter(
                api=api,
                interval_seconds=SCREENSHOT_INTERVAL_SECONDS,
                logger=logger,
            )
            _shared_reporter.start()


def release_screenshots(logger=None) -> None:
    global _shared_reporter, _shared_refs

    reporter = None
    with _shared_lock:
        _shared_refs = max(0, _shared_refs - 1)

        if _shared_refs == 0 and _shared_reporter is not None:
            reporter = _shared_reporter
            _shared_reporter = None

    # Parar (com print final) fora do lock: outra task que inicia agora não espera por isso.
    if reporter is not None:
        reporter.stop(send_final=True)
