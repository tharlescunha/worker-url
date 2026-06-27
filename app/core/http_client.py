from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from app.core.constants import DEFAULT_HTTP_TIMEOUT
from app.core.exceptions import ApiRequestError, AuthenticationError


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

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        delay = _RETRY_BASE_DELAY

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                return self._handle_response(response)
            except AuthenticationError:
                raise
            except ApiRequestError as exc:
                if not _is_retryable_api_error(exc):
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = ApiRequestError(f"Erro GET em {url}: {exc}")

            if attempt < _RETRY_ATTEMPTS:
                time.sleep(delay)
                delay *= _RETRY_BACKOFF

        raise last_exc or ApiRequestError(f"GET {url} falhou após {_RETRY_ATTEMPTS} tentativas.")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        delay = _RETRY_BASE_DELAY

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                response = self.session.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                return self._handle_response(response)
            except AuthenticationError:
                raise
            except ApiRequestError as exc:
                if not _is_retryable_api_error(exc):
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = ApiRequestError(f"Erro POST em {url}: {exc}")

            if attempt < _RETRY_ATTEMPTS:
                time.sleep(delay)
                delay *= _RETRY_BACKOFF

        raise last_exc or ApiRequestError(f"POST {url} falhou após {_RETRY_ATTEMPTS} tentativas.")

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        delay = _RETRY_BASE_DELAY

        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                response = self.session.patch(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                return self._handle_response(response)
            except AuthenticationError:
                raise
            except ApiRequestError as exc:
                if not _is_retryable_api_error(exc):
                    raise
                last_exc = exc
            except requests.RequestException as exc:
                last_exc = ApiRequestError(f"Erro PATCH em {url}: {exc}")

            if attempt < _RETRY_ATTEMPTS:
                time.sleep(delay)
                delay *= _RETRY_BACKOFF

        raise last_exc or ApiRequestError(f"PATCH {url} falhou após {_RETRY_ATTEMPTS} tentativas.")

    @staticmethod
    def _handle_response(response: requests.Response) -> dict[str, Any]:
        data: dict[str, Any] = {}

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            try:
                data = response.json()
            except Exception:
                data = {}

        if response.status_code in (401, 403):
            detail = data.get("detail") or response.text or "Não autorizado."
            raise AuthenticationError(detail)

        if not response.ok:
            detail = data.get("detail") or response.text or "Erro na API."
            raise ApiRequestError(f"HTTP {response.status_code}: {detail}")

        return data


def _is_retryable_api_error(exc: ApiRequestError) -> bool:
    msg = str(exc)
    return any(f"HTTP {s}" in msg for s in _RETRYABLE_STATUS)
