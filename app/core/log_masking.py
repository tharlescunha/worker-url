from __future__ import annotations

import logging
import re

# https://usuario:token@host/... -> https://***@host/...
# Cobre a URL de clone do Azure DevOps/GitHub com PAT embutido.
_URL_CREDENTIALS_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")


def mask_url_credentials(text: str) -> str:
    if not text or "@" not in text:
        return text
    return _URL_CREDENTIALS_RE.sub(r"\1***@", text)


class UrlCredentialsFilter(logging.Filter):
    """Mascara credenciais em URLs antes de qualquer handler gravar ou enviar a linha."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        masked = mask_url_credentials(message)
        if masked != message:
            record.msg = masked
            record.args = None

        # O traceback só seria montado depois, no handler: monta aqui para poder mascarar.
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
            except Exception:
                pass
        if record.exc_text:
            record.exc_text = mask_url_credentials(record.exc_text)
        return True


_TRACEBACK_FORMATTER = logging.Formatter()
