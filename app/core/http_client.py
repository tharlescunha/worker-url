from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from app.core.constants import DEFAULT_HTTP_TIMEOUT
from app.core.exceptions import ApiRequestError, AuthenticationError, ForbiddenError


# Erros HTTP que valem retry (servidor sobrecarregado / temporariamente indisponível)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_RETRY_BACKOFF = 2.0


@dataclass
class HttpClient:
    base_url: str
    timeout: int = DEFAULT_HTTP_TIMEOUT
    access_token: str | None = None

    # Chamado em 401 com o token que falhou; deve renovar o token do client e
    # devolver True se conseguiu. Permite que tasks longas continuem após o
    # token expirar no meio da execução.
    on_unauthorized: Callable[[str | None], bool] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        return headers

    def set_token(self, access_token: str) -> None:
        self.access_token = access_token

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, retry_auth=retry_auth)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        return self._request("POST", path, payload=payload, retry_auth=retry_auth)

    def patch(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        return self._request("PATCH", path, payload=payload, retry_auth=retry_auth)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        try:
            return self._request_with_retry(method, path, params=params, payload=payload)
        except AuthenticationError:
            hook = self.on_unauthorized
            if not retry_auth or hook is None:
                raise

            failed_token = self.access_token
            try:
                refreshed = bool(hook(failed_token))
            except Exception:
                refreshed = False

            if not refreshed:
                raise

            return self._request_with_retry(method, path, params=params, payload=payload)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        delay = _RETRY_BASE_DELAY

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                return self._handle_response(response)
            except (AuthenticationError, ForbiddenError):
                raise
            except ApiRequestError as exc:
                if not _is_retryable_api_error(exc):
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = ApiRequestError(f"Erro {method} em {url}: {exc}")

            if attempt < _RETRY_ATTEMPTS:
                time.sleep(delay)
                delay *= _RETRY_BACKOFF

        raise last_exc or ApiRequestError(
            f"{method} {url} falhou após {_RETRY_ATTEMPTS} tentativas."
        )

    @staticmethod
    def _handle_response(response: requests.Response) -> dict[str, Any]:
        data: dict[str, Any] = {}

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            try:
                data = response.json()
            except Exception:
                data = {}

        if not isinstance(data, dict):
            data = {}

        detail = data.get("detail") or response.text or ""

        if response.status_code == 401:
            raise AuthenticationError(detail or "Não autorizado.")

        # 403 NÃO é token expirado: o backend recusou a operação
        # (runner desativado, automação não vinculada etc.).
        if response.status_code == 403:
            raise ForbiddenError(f"HTTP 403: {detail or 'Acesso negado.'}")

        if not response.ok:
            raise ApiRequestError(f"HTTP {response.status_code}: {detail or 'Erro na API.'}")

        return data


def _is_retryable_api_error(exc: ApiRequestError) -> bool:
    msg = str(exc)
    return any(f"HTTP {s}" in msg for s in _RETRYABLE_STATUS)
