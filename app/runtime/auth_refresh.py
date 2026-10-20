from __future__ import annotations

import threading
from datetime import datetime, timezone

from app.core.config_models import AuthData
from app.core.constants import AUTH_FILE
from app.core.http_client import HttpClient
from app.core.json_store import save_model
from app.core.security import protect_text, unprotect_text

REFRESH_PATH = "/api/v1/auth/refresh"


class TokenRefresher:
    """
    Renova o access_token usando o refresh_token salvo.

    É usado como hook `on_unauthorized` do HttpClient compartilhado, então
    qualquer thread (inclusive tasks longas) que receba 401 renova o token
    uma única vez e repete a chamada.
    """

    def __init__(self, auth: AuthData, client: HttpClient, logger) -> None:
        self.auth = auth
        self.client = client
        self.logger = logger
        self._lock = threading.Lock()

    def __call__(self, failed_token: str | None) -> bool:
        with self._lock:
            # Outra thread já renovou enquanto esta esperava o lock.
            if failed_token and self.client.access_token and self.client.access_token != failed_token:
                return True

            return self._refresh()

    def _refresh(self) -> bool:
        if not self.auth.encrypted_refresh_token:
            self.logger.error(
                "Token expirado e refresh_token não disponível — reregistre o worker."
            )
            return False

        try:
            refresh_token = unprotect_text(self.auth.encrypted_refresh_token)
        except Exception as exc:
            self.logger.error("Falha ao descriptografar refresh_token: %s", exc)
            return False

        try:
            response = self.client.post(
                REFRESH_PATH,
                {"refresh_token": refresh_token},
                retry_auth=False,
            )
        except Exception as exc:
            self.logger.error("Falha ao chamar endpoint de refresh: %s", exc)
            return False

        new_access = response.get("access_token")
        new_refresh = response.get("refresh_token")

        if not new_access:
            self.logger.error("Endpoint de refresh não retornou access_token.")
            return False

        self.client.set_token(new_access)

        try:
            self.auth.encrypted_access_token = protect_text(new_access)
            if new_refresh:
                self.auth.encrypted_refresh_token = protect_text(new_refresh)
            self.auth.saved_at = datetime.now(timezone.utc)
            save_model(AUTH_FILE, self.auth)
            self.logger.info("Token renovado com sucesso e salvo em auth.json.")
        except Exception as exc:
            # Token atualizado em memória; segue funcionando até o próximo restart.
            self.logger.warning("Token renovado mas falha ao salvar auth.json: %s", exc)

        return True
