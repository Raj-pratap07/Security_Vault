from __future__ import annotations

from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError, ServiceUnavailable

from app.core.errors import ConfigurationError
from app.storage.base import BaseStorageAdapter, DeleteResult, HealthResult, PutResult, ShareEnvelope


class FirebaseAdapter(BaseStorageAdapter):
    provider_id = "firebase"

    def __init__(
        self,
        *,
        project_id: str,
        collection: str,
        credentials_path: str = "",
        timeout_seconds: float = 10.0,
        client: Any | None = None,
    ):
        self.collection_name = collection
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self.client = client
            return
        if not project_id or not collection:
            raise ValueError("Firebase project and collection configuration are required")
        credential = None
        if credentials_path:
            path = Path(credentials_path)
            if not path.is_file():
                raise ConfigurationError("Firebase credential file was not found")
            try:
                credential = credentials.Certificate(str(path))
            except (OSError, ValueError):
                raise ConfigurationError("Firebase credential file is invalid") from None
        app_name = f"firefly-{project_id}"
        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            app = firebase_admin.initialize_app(
                credential,
                options={"projectId": project_id},
                name=app_name,
            )
        self.client = firestore.client(app=app)
        self._collection = self.client.collection(collection)

    @staticmethod
    def _to_document(envelope: ShareEnvelope) -> dict[str, Any]:
        return {
            "record_id": envelope.record_id,
            "version": envelope.version,
            "share_index": envelope.share_index,
            "provider_id": envelope.provider_id,
            "key_ref": envelope.key_ref,
            "format_version": envelope.format_version,
            "ciphertext": envelope.ciphertext,
        }

    def put_share(self, share_id: str, envelope: ShareEnvelope) -> PutResult:
        try:
            self._collection.document(share_id).set(
                self._to_document(envelope),
                timeout=self.timeout_seconds,
            )
            return PutResult(self.provider_id, share_id, "STORED", True)
        except (DeadlineExceeded, ServiceUnavailable):
            return PutResult(self.provider_id, share_id, "UNKNOWN", False)
        except GoogleAPICallError:
            return PutResult(self.provider_id, share_id, "WRITE_FAILED", False)

    def get_share(self, share_id: str) -> ShareEnvelope | None:
        try:
            snapshot = self._collection.document(share_id).get(timeout=self.timeout_seconds)
            if not snapshot.exists:
                return None
            payload = snapshot.to_dict()
            return ShareEnvelope(
                record_id=payload["record_id"],
                version=int(payload["version"]),
                share_index=int(payload["share_index"]),
                provider_id=payload["provider_id"],
                key_ref=payload["key_ref"],
                format_version=int(payload["format_version"]),
                ciphertext=payload["ciphertext"],
            )
        except (GoogleAPICallError, KeyError, TypeError, ValueError):
            raise RuntimeError("Firestore share lookup failed") from None

    def delete_share(self, share_id: str) -> DeleteResult:
        try:
            reference = self._collection.document(share_id)
            snapshot = reference.get(timeout=self.timeout_seconds)
            reference.delete(timeout=self.timeout_seconds)
            existed = bool(snapshot.exists)
            return DeleteResult(
                self.provider_id,
                share_id,
                "DELETE_CONFIRMED" if existed else "CONFIRMED_ABSENT",
                deleted=existed,
                existed=existed,
            )
        except (DeadlineExceeded, ServiceUnavailable):
            return DeleteResult(self.provider_id, share_id, "UNKNOWN", False, False)
        except GoogleAPICallError:
            return DeleteResult(self.provider_id, share_id, "DELETE_FAILED", False, False)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def health_check(self) -> HealthResult:
        try:
            self._collection.limit(1).get(timeout=self.timeout_seconds)
            return HealthResult(self.provider_id, "ok", True)
        except GoogleAPICallError:
            return HealthResult(self.provider_id, "unavailable", False)
