from __future__ import annotations

import base64
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

import requests

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, ProviderError


class AkeylessService:
    def __init__(
        self,
        key_refs: Mapping[str, str] | None = None,
        *,
        settings: Settings | None = None,
        api_url: str | None = None,
        access_id: str | None = None,
        access_key: str | None = None,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5.0, 15.0),
    ):
        self.settings = settings or get_settings()
        self.key_refs = dict(self.settings.akeyless_key_refs)
        if key_refs is not None:
            self.key_refs.update(key_refs)
        self.api_url = (api_url or self.settings.akeyless_api_url).rstrip("/")
        self.access_id = access_id if access_id is not None else self.settings.akeyless_access_id
        self.access_key = access_key if access_key is not None else self.settings.akeyless_access_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def build_encryption_context(
        *,
        record_id: str,
        version: int,
        share_index: int,
        provider_id: str,
        format_version: int = 1,
    ) -> Dict[str, str]:
        return {
            "record_id": record_id,
            "version": str(version),
            "share_index": str(share_index),
            "provider_id": provider_id,
            "format_version": str(format_version),
        }

    def validate_key_reference(self, provider_id: str, key_ref: str) -> None:
        expected = self.key_refs.get(provider_id)
        if not expected or expected != key_ref:
            raise ConfigurationError("Configured Akeyless key reference is invalid")

    def _get_token(self) -> str:
        with self._token_lock:
            return self._get_token_locked()

    def _get_token_locked(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.access_id or not self.access_key:
            raise ConfigurationError("Akeyless access credentials are not configured")
        try:
            response = self.session.post(
                f"{self.api_url}/auth",
                json={
                    "access-type": "access_key",
                    "access-id": self.access_id,
                    "access-key": self.access_key,
                    "json": True,
                },
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ProviderError("Akeyless authentication failed")
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError("Akeyless authentication returned an invalid response")
            token = body.get("token")
            if not isinstance(token, str) or not token:
                raise ProviderError("Akeyless authentication returned an invalid response")
            expiration = body.get("expiration")
            cache_seconds = 60.0
            if isinstance(expiration, str):
                try:
                    expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    cache_seconds = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds() - 60.0)
                except ValueError:
                    pass
            self._token = token
            self._token_expires_at = time.monotonic() + cache_seconds
            return token
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ProviderError("Akeyless authentication request failed") from None

    def _post_operation(self, operation: str, payload: dict[str, Any]) -> str:
        try:
            response = self.session.post(
                f"{self.api_url}/{operation}",
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code != 200:
                raise ProviderError(f"Akeyless {operation} operation failed")
            body = response.json()
            if not isinstance(body, dict):
                raise ProviderError(f"Akeyless {operation} returned an invalid response")
            result = body.get("result")
            if not isinstance(result, str):
                raise ProviderError(f"Akeyless {operation} returned an invalid response")
            return result
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ProviderError(f"Akeyless {operation} request failed") from None

    def encrypt_share(
        self,
        share_bytes: bytes,
        *,
        record_id: str,
        version: int,
        share_index: int,
        provider_id: str,
        key_ref: str,
        format_version: int = 1,
    ) -> str:
        self.validate_key_reference(provider_id, key_ref)
        context = self.build_encryption_context(
            record_id=record_id,
            version=version,
            share_index=share_index,
            provider_id=provider_id,
            format_version=format_version,
        )
        return self._post_operation(
            "encrypt",
            {
                "key-name": key_ref,
                "token": self._get_token(),
                "plaintext": base64.b64encode(share_bytes).decode("ascii"),
                "input-format": "base64",
                "encryption-context": context,
            },
        )

    def decrypt_share(
        self,
        ciphertext: str,
        *,
        record_id: str,
        version: int,
        share_index: int,
        provider_id: str,
        key_ref: str,
        format_version: int = 1,
    ) -> bytes:
        self.validate_key_reference(provider_id, key_ref)
        context = self.build_encryption_context(
            record_id=record_id,
            version=version,
            share_index=share_index,
            provider_id=provider_id,
            format_version=format_version,
        )
        result = self._post_operation(
            "decrypt",
            {
                "key-name": key_ref,
                "token": self._get_token(),
                "ciphertext": ciphertext,
                "output-format": "base64",
                "encryption-context": context,
            },
        )
        try:
            return base64.b64decode(result, validate=True)
        except (ValueError, TypeError):
            raise ProviderError("Akeyless returned invalid Base64 plaintext") from None
