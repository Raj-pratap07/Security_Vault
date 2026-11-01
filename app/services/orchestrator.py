from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from app.core.errors import RecoveryError
from app.db.repository import SecretRepository
from app.services.akeyless_service import AkeylessService
from app.services.sss_service import SSSService, ShareBundle
from app.storage.base import BaseStorageAdapter, PutResult, ShareEnvelope, build_share_id
from app.storage.registry import ProviderRegistry

logger = logging.getLogger("firefly.orchestrator")


class StorageOrchestrator:
    def __init__(
        self,
        *,
        repository: SecretRepository,
        sss_service: SSSService,
        akeyless_service: AkeylessService,
        provider_registry: ProviderRegistry,
    ):
        self.repository = repository
        self.sss_service = sss_service
        self.akeyless_service = akeyless_service
        self.provider_registry = provider_registry
        self.provider_order = list(provider_registry.provider_ids())
        if (
            len(self.provider_order) != self.sss_service.share_count
            or len(set(self.provider_order)) != len(self.provider_order)
        ):
            raise ValueError("Provider count must match the configured Shamir share count")
        if self.sss_service.threshold > self.sss_service.share_count:
            raise ValueError("Shamir threshold cannot exceed the configured share count")
        if (
            self.repository.share_count != self.sss_service.share_count
            or self.repository.threshold != self.sss_service.threshold
        ):
            raise ValueError("Metadata and Shamir profiles must match")
        if any(
            provider_id not in self.akeyless_service.key_refs
            for provider_id in self.provider_order
        ):
            raise ValueError("Each active provider must have a configured Akeyless key reference")

    def close(self) -> None:
        self.provider_registry.close()
        self.akeyless_service.close()

    def create_record(self, owner_id: str) -> Dict[str, Any]:
        record_id = uuid.uuid4().hex
        version = 1
        share_rows = []
        for share_index, provider_id in enumerate(self.provider_order, start=1):
            share_id = build_share_id(record_id, version, share_index)
            share_rows.append(
                {
                    "share_index": share_index,
                    "provider_id": provider_id,
                    "share_id": share_id,
                    "key_ref": self.akeyless_service.key_refs[provider_id],
                }
            )
        self.repository.create_pending_operation(
            record_id=record_id,
            owner_id=owner_id,
            version=version,
            share_config=share_rows,
            share_count=self.sss_service.share_count,
            threshold=self.sss_service.threshold,
        )
        return {"record_id": record_id, "version": version}

    def store_secret(self, owner_id: str, secret: str) -> Dict[str, Any]:
        record = self.create_record(owner_id)
        record_id = record["record_id"]
        version = record["version"]
        try:
            shares = self.sss_service.split_secret(secret)
            if len(shares) != len(self.provider_order):
                raise RecoveryError("Secret sharing returned an invalid share count")
            for share_index, (share, provider_id) in enumerate(zip(shares, self.provider_order), start=1):
                share_bytes = share.to_bytes()
                key_ref = self.akeyless_service.key_refs[provider_id]
                ciphertext = self.akeyless_service.encrypt_share(
                    share_bytes,
                    record_id=record_id,
                    version=version,
                    share_index=share_index,
                    provider_id=provider_id,
                    key_ref=key_ref,
                )
                share_id = build_share_id(record_id, version, share_index)
                envelope = ShareEnvelope(
                    record_id=record_id,
                    version=version,
                    share_index=share_index,
                    provider_id=provider_id,
                    key_ref=key_ref,
                    format_version=1,
                    ciphertext=ciphertext,
                )
                provider = self.provider_registry.get(provider_id)
                result = self._put_and_verify(
                    provider=provider,
                    share_id=share_id,
                    envelope=envelope,
                    record_id=record_id,
                    version=version,
                    share_index=share_index,
                )
                if not result.success:
                    share_status = "UNKNOWN" if result.status == "UNKNOWN" else "WRITE_FAILED"
                    self.repository.update_share_status(
                        record_id=record_id,
                        version=version,
                        share_index=share_index,
                        status=share_status,
                        last_error_code=share_status,
                    )
                    raise RecoveryError("One or more provider writes failed")
                self.repository.update_share_status(
                    record_id=record_id,
                    version=version,
                    share_index=share_index,
                    status="STORED",
                )
            available = self.repository.set_record_status(record_id=record_id, status="AVAILABLE")
            return {
                "record_id": record_id,
                "status": "AVAILABLE",
                "share_count": self.sss_service.share_count,
                "threshold": self.sss_service.threshold,
                "created_at": available["created_at"],
            }
        except Exception:
            self._rollback_preserving_error(record_id)
            raise

    def _put_and_verify(
        self,
        *,
        provider: BaseStorageAdapter,
        share_id: str,
        envelope: ShareEnvelope,
        record_id: str,
        version: int,
        share_index: int,
    ) -> PutResult:
        """Confirm each write with a deterministic-ID read-back before STORED."""
        last_status = "WRITE_FAILED"
        for attempt in range(2):
            self.repository.record_write_attempt(
                record_id=record_id, version=version, share_index=share_index
            )
            result = provider.put_share(share_id, envelope)
            last_status = result.status
            if not result.success and result.status != "UNKNOWN":
                return result
            if result.status == "UNKNOWN":
                self.repository.update_share_status(
                    record_id=record_id,
                    version=version,
                    share_index=share_index,
                    status="UNKNOWN",
                    last_error_code="WRITE_OUTCOME_UNKNOWN",
                )
            stored = None
            for lookup_attempt in range(2):
                try:
                    stored = provider.get_share(share_id)
                    break
                except Exception:
                    if lookup_attempt == 1:
                        return PutResult(provider.provider_id, share_id, "UNKNOWN", False)
            if stored == envelope:
                return PutResult(provider.provider_id, share_id, "STORED", True)
            if stored is not None:
                return PutResult(provider.provider_id, share_id, "WRITE_FAILED", False)
            if attempt == 1:
                break
            # A confirmed absence allows one idempotent retry at the same ID.

        final_status = "UNKNOWN" if last_status == "UNKNOWN" else "WRITE_FAILED"
        return PutResult(provider.provider_id, share_id, final_status, False)

    def rollback(self, record_id: str) -> None:
        record = self.repository.get_record(record_id)
        cleanup_complete = True
        for share in self.repository.get_share_records_for_record(record_id, record["version"]):
            try:
                result = self.provider_registry.get(share["provider_id"]).delete_share(share["share_id"])
            except Exception:
                result = None
            if result is not None and (
                result.deleted or result.status == "CONFIRMED_ABSENT"
            ):
                self.repository.update_share_status(
                    record_id=record_id,
                    version=record["version"],
                    share_index=share["share_index"],
                    status="DELETE_CONFIRMED",
                )
            else:
                cleanup_complete = False
                self.repository.update_share_status(
                    record_id=record_id,
                    version=record["version"],
                    share_index=share["share_index"],
                    status="DELETE_PENDING",
                    last_error_code="DELETE_UNCONFIRMED",
                )
        self.repository.set_record_status(
            record_id=record_id,
            status="FAILED" if cleanup_complete else "CLEANUP_REQUIRED",
            last_error_code=None if cleanup_complete else "CLEANUP_UNCONFIRMED",
        )

    def _rollback_preserving_error(self, record_id: str) -> None:
        """Run rollback from an error path without replacing the original failure."""
        try:
            self.rollback(record_id)
        except Exception as exc:
            logger.error("rollback failed record_id=%s error=%s", record_id, type(exc).__name__)
            try:
                self.repository.set_record_status(
                    record_id=record_id,
                    status="CLEANUP_REQUIRED",
                    last_error_code="ROLLBACK_FAILED",
                )
            except Exception as status_exc:
                logger.error(
                    "could not persist CLEANUP_REQUIRED record_id=%s error=%s",
                    record_id,
                    type(status_exc).__name__,
                )

    def load_valid_share(self, record: Dict[str, Any], share: Dict[str, Any]) -> ShareBundle | None:
        """Fetch, validate and decrypt one stored share; None if it is not a valid share."""
        record_id = record["record_id"]
        try:
            provider = self.provider_registry.get(share["provider_id"])
            envelope = provider.get_share(share["share_id"])
            if envelope is None:
                return None
            self.akeyless_service.validate_key_reference(share["provider_id"], share["key_ref"])
            if (
                envelope.record_id != record_id
                or envelope.version != share["version"]
                or envelope.share_index != share["share_index"]
                or envelope.provider_id != share["provider_id"]
                or envelope.key_ref != share["key_ref"]
                or envelope.format_version != 1
            ):
                return None
            plaintext = self.akeyless_service.decrypt_share(
                envelope.ciphertext,
                record_id=record_id,
                version=share["version"],
                share_index=share["share_index"],
                provider_id=share["provider_id"],
                key_ref=share["key_ref"],
            )
            bundle = self.sss_service.decode_share(plaintext)
        except Exception:
            return None
        if bundle.index != share["share_index"]:
            return None
        return bundle

    def recover_secret(self, record_id: str, owner_id: str) -> str:
        return self._recover_secret_from_provider_ids(record_id, owner_id, None)

    def _recover_secret_from_provider_ids(
        self,
        record_id: str,
        owner_id: str,
        provider_ids: set[str] | None,
    ) -> str:
        record = self.repository.get_record_for_owner(record_id, owner_id)
        if record["status"] != "AVAILABLE":
            raise RecoveryError("Record is not available for recovery")
        if provider_ids is not None:
            if (
                len(provider_ids) < record["threshold"]
                or not provider_ids.issubset(set(self.provider_order))
            ):
                raise RecoveryError("Provider selection is invalid")
        threshold = record["threshold"]
        valid_shares: list[ShareBundle] = []
        for share in self.repository.get_share_records_for_record(record_id, record["version"]):
            if share["status"] != "STORED":
                continue
            if provider_ids is not None and share["provider_id"] not in provider_ids:
                continue
            bundle = self.load_valid_share(record, share)
            if bundle is None or any(candidate.index == bundle.index for candidate in valid_shares):
                continue
            valid_shares.append(bundle)
            if len(valid_shares) == threshold:
                break
        if len(valid_shares) < threshold:
            raise RecoveryError(
                f"Fewer than {threshold} valid shares were available for recovery"
            )
        try:
            secret_bytes = self.sss_service.recover_secret(valid_shares)
        except ValueError:
            raise RecoveryError("Valid shares could not be combined") from None
        try:
            return secret_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise RecoveryError("Recovered secret encoding is invalid") from None

    def get_status(self, record_id: str, owner_id: str) -> Dict[str, Any]:
        record = self.repository.get_record_for_owner(record_id, owner_id)
        shares = self.repository.get_share_records_for_record(record_id, record["version"])
        confirmed = sum(1 for share in shares if share["status"] == "STORED")
        info = dict(record)
        info["confirmed_shares"] = confirmed
        return info

    def reconcile_pending(self) -> Dict[str, int]:
        """Resolve unfinished records left by a crash. Safe to run on every start.

        PENDING records are promoted only when every share is re-read from its
        deterministic provider ID, authenticated by Akeyless and mutually
        consistent. Anything else (and any CLEANUP_REQUIRED record) is rolled
        back; a record stays CLEANUP_REQUIRED until deletion is confirmed.
        """
        summary = {"examined": 0, "available": 0, "failed": 0, "cleanup_required": 0, "unresolved": 0}
        for record in self.repository.list_pending_records():
            summary["examined"] += 1
            try:
                summary[self._reconcile_record(record)] += 1
            except Exception as exc:
                summary["unresolved"] += 1
                logger.error(
                    "reconciliation failed record_id=%s error=%s",
                    record["record_id"],
                    type(exc).__name__,
                )
        logger.info(
            "startup reconciliation examined=%d available=%d failed=%d cleanup_required=%d unresolved=%d",
            summary["examined"],
            summary["available"],
            summary["failed"],
            summary["cleanup_required"],
            summary["unresolved"],
        )
        return summary

    def _reconcile_record(self, record: Dict[str, Any]) -> str:
        record_id = record["record_id"]
        version = record["version"]
        if record["status"] == "PENDING":
            shares = self.repository.get_share_records_for_record(record_id, version)
            if self._share_set_is_complete(record, shares):
                for share in shares:
                    self.repository.update_share_status(
                        record_id=record_id,
                        version=version,
                        share_index=share["share_index"],
                        status="STORED",
                    )
                self.repository.set_record_status(record_id=record_id, status="AVAILABLE")
                return "available"
        self.rollback(record_id)
        final = self.repository.get_record(record_id)["status"]
        return "failed" if final == "FAILED" else "cleanup_required"

    def _share_set_is_complete(self, record: Dict[str, Any], shares: list[Dict[str, Any]]) -> bool:
        if len(shares) != record["share_count"]:
            return False
        bundles = [self.load_valid_share(record, share) for share in shares]
        if any(bundle is None for bundle in bundles):
            return False
        # Adjacent pairs agreeing proves all shares lie on one polynomial.
        try:
            reference = self.sss_service.recover_secret(bundles[:2])
            return all(
                self.sss_service.recover_secret(bundles[i : i + 2]) == reference
                for i in range(1, len(bundles) - 1)
            )
        except ValueError:
            return False
