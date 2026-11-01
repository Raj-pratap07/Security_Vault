from __future__ import annotations

import json

import requests

from app.storage.base import BaseStorageAdapter, DeleteResult, HealthResult, PutResult, ShareEnvelope


class UpstashAdapter(BaseStorageAdapter):
    provider_id = "upstash"

    def __init__(
        self,
        *,
        rest_url: str,
        rest_token: str,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
    ):
        self.rest_url = rest_url.rstrip("/")
        self.rest_token = rest_token
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def _command(self, command: list[str]):
        if not self.rest_url or not self.rest_token:
            raise RuntimeError("Upstash REST configuration is missing")
        return self.session.post(
            self.rest_url,
            json=command,
            headers={"Authorization": f"Bearer {self.rest_token}"},
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _serialize(envelope: ShareEnvelope) -> str:
        return json.dumps(
            {
                "record_id": envelope.record_id,
                "version": envelope.version,
                "share_index": envelope.share_index,
                "provider_id": envelope.provider_id,
                "key_ref": envelope.key_ref,
                "format_version": envelope.format_version,
                "ciphertext": envelope.ciphertext,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize(value: str) -> ShareEnvelope:
        payload = json.loads(value)
        return ShareEnvelope(
            record_id=payload["record_id"],
            version=int(payload["version"]),
            share_index=int(payload["share_index"]),
            provider_id=payload["provider_id"],
            key_ref=payload["key_ref"],
            format_version=int(payload["format_version"]),
            ciphertext=payload["ciphertext"],
        )

    def put_share(self, share_id: str, envelope: ShareEnvelope) -> PutResult:
        try:
            response = self._command(["SET", share_id, self._serialize(envelope)])
            if response.status_code != 200:
                return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)
            if response.json().get("result") != "OK":
                return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)
            return PutResult(self.provider_id, share_id, "STORED", True)
        except requests.Timeout:
            return PutResult(self.provider_id, share_id, "UNKNOWN", False)
        except requests.RequestException:
            return PutResult(self.provider_id, share_id, "UNKNOWN", False)
        except (ValueError, TypeError):
            return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)

    def get_share(self, share_id: str) -> ShareEnvelope | None:
        try:
            response = self._command(["GET", share_id])
            if response.status_code != 200:
                raise RuntimeError("Upstash share lookup failed")
            value = response.json().get("result")
            if value is None:
                return None
            if not isinstance(value, str):
                raise RuntimeError("Upstash returned an invalid share record")
            return self._deserialize(value)
        except (requests.RequestException, ValueError, TypeError, KeyError):
            raise RuntimeError("Upstash share lookup failed") from None

    def delete_share(self, share_id: str) -> DeleteResult:
        try:
            response = self._command(["DEL", share_id])
            if response.status_code != 200:
                return DeleteResult(self.provider_id, share_id, "DELETE_FAILED", False, False)
            existed = bool(int(response.json().get("result", 0)))
            return DeleteResult(
                self.provider_id,
                share_id,
                "DELETE_CONFIRMED" if existed else "CONFIRMED_ABSENT",
                deleted=existed,
                existed=existed,
            )
        except requests.RequestException:
            return DeleteResult(self.provider_id, share_id, "UNKNOWN", False, False)
        except (ValueError, TypeError):
            return DeleteResult(self.provider_id, share_id, "DELETE_FAILED", False, False)

    def close(self) -> None:
        self.session.close()

    def health_check(self) -> HealthResult:
        try:
            response = self._command(["PING"])
            ok = response.status_code == 200 and response.json().get("result") == "PONG"
        except (requests.RequestException, ValueError, TypeError):
            ok = False
        return HealthResult(self.provider_id, "ok" if ok else "unavailable", ok)
