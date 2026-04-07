from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.core.constants import DEFAULT_HTTP_TIMEOUT
from app.core.exceptions import ApiRequestError, AuthenticationError


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

        try:
            response = self.session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            return self._handle_response(response)
        except requests.RequestException as exc:
            raise ApiRequestError(f"Erro GET em {url}: {exc}") from exc

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"

        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            return self._handle_response(response)
        except requests.RequestException as exc:
            raise ApiRequestError(f"Erro POST em {url}: {exc}") from exc

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"

        try:
            response = self.session.patch(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            return self._handle_response(response)
        except requests.RequestException as exc:
            raise ApiRequestError(f"Erro PATCH em {url}: {exc}") from exc

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
    