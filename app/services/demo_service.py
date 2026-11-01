from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import threading
import time
from itertools import combinations
from typing import Any, Callable, Dict, List

from app.core.errors import RecordNotFoundError, RecoveryError
from app.services.orchestrator import StorageOrchestrator
from app.services.sss_service import ShareBundle
from app.storage.base import build_share_id


class DemoService:
    """Backs the /demo endpoints: collect shares one at a time, then try to rebuild.

    Decrypted shares are held in process memory only (the backend is trusted with
    plaintext in this threat model), keyed by record, bounded in number and age, and
    dropped on cleanup. Nothing here is persisted.
    """

    def __init__(
        self,
        orchestrator: StorageOrchestrator,
        *,
        ttl_seconds: float = 900.0,
        max_records: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.orchestrator = orchestrator
        self.ttl_seconds = ttl_seconds
        self.max_records = max_records
        self._clock = clock
        self._lock = threading.Lock()
        self._held: Dict[str, Dict[str, Any]] = {}

    # -- helpers ----------------------------------------------------------------

    def _evict(self) -> None:
        now = self._clock()
        for record_id in [r for r, entry in self._held.items() if now - entry["touched"] > self.ttl_seconds]:
            del self._held[record_id]
        while len(self._held) > self.max_records:
            oldest = min(self._held, key=lambda r: self._held[r]["touched"])
            del self._held[oldest]

    def _owned_record(self, record_id: str, owner_id: str) -> Dict[str, Any]:
        return self.orchestrator.repository.get_record_for_owner(record_id, owner_id)

    def _held_shares(self, record_id: str) -> Dict[int, ShareBundle]:
        with self._lock:
            self._evict()
            entry = self._held.get(record_id)
            return dict(entry["shares"]) if entry else {}

    # -- operations -------------------------------------------------------------

    def list_shares(self, record_id: str, owner_id: str) -> List[Dict[str, Any]]:
        """What each cloud stores for this record: ciphertext preview only, never plaintext."""
        record = self._owned_record(record_id, owner_id)
        shares = []
        for row in self.orchestrator.repository.get_share_records_for_record(record_id, record["version"]):
            try:
                envelope = self.orchestrator.provider_registry.get(row["provider_id"]).get_share(row["share_id"])
            except Exception:
                envelope = None
            shares.append(
                {
                    "share_index": row["share_index"],
                    "provider_id": row["provider_id"],
                    "status": row["status"],
                    "ciphertext_preview": envelope.ciphertext[:24] if envelope else None,
                }
            )
        return shares

    def collect(self, record_id: str, owner_id: str, share_index: int) -> Dict[str, Any]:
        record = self._owned_record(record_id, owner_id)
        if record["status"] != "AVAILABLE":
            raise RecoveryError("Record is not available for recovery")
        rows = {
            row["share_index"]: row
            for row in self.orchestrator.repository.get_share_records_for_record(record_id, record["version"])
        }
        if share_index not in rows or rows[share_index]["status"] != "STORED":
            raise RecordNotFoundError("Share not found")
        started = self._clock()
        bundle = self.orchestrator.load_valid_share(record, rows[share_index])
        latency_ms = (self._clock() - started) * 1000
        if bundle is None:
            raise RecoveryError("Share could not be fetched and validated")
        with self._lock:
            self._evict()
            entry = self._held.setdefault(record_id, {"shares": {}, "touched": self._clock()})
            entry["shares"][share_index] = bundle
            entry["touched"] = self._clock()
            held = sorted(entry["shares"])
            self._evict()
        return {
            "share_index": share_index,
            "provider_id": rows[share_index]["provider_id"],
            "fingerprint": hashlib.sha256(bundle.to_bytes()).hexdigest()[:8],
            "latency_ms": round(latency_ms, 1),
            "shares_held": held,
        }

    def export_share(self, record_id: str, owner_id: str, share_index: int) -> Dict[str, Any]:
        """A 'share card': one decrypted share, to hand to a share holder (demo only)."""
        record = self._owned_record(record_id, owner_id)
        if record["status"] != "AVAILABLE":
            raise RecoveryError("Record is not available for recovery")
        rows = {
            row["share_index"]: row
            for row in self.orchestrator.repository.get_share_records_for_record(record_id, record["version"])
        }
        if share_index not in rows or rows[share_index]["status"] != "STORED":
            raise RecordNotFoundError("Share not found")
        bundle = self.orchestrator.load_valid_share(record, rows[share_index])
        if bundle is None:
            raise RecoveryError("Share could not be fetched and validated")
        return {
            "record_id": record_id,
            "share_index": share_index,
            "threshold": record["threshold"],
            "share_count": record["share_count"],
            "share_b64": base64.b64encode(bundle.to_bytes()).decode("ascii"),
        }

    def fetch_with_shares(self, record_id: str, owner_id: str, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Holders present share cards and ask for the secret.

        Each card is compared with the genuine share (loaded from its cloud and decrypted),
        so forged, altered or repeated cards earn nothing. The secret is returned only when
        at least `threshold` distinct genuine shares were presented.
        """
        record = self._owned_record(record_id, owner_id)
        if record["status"] != "AVAILABLE":
            raise RecoveryError("Record is not available for recovery")
        threshold = record["threshold"]
        rows = {
            row["share_index"]: row
            for row in self.orchestrator.repository.get_share_records_for_record(record_id, record["version"])
        }
        genuine: Dict[int, ShareBundle] = {}
        invalid: List[int] = []
        presented: Dict[int, bytes] = {}
        for card in cards:
            index = card["share_index"]
            if index in presented:
                continue  # the same share twice still counts once
            try:
                presented[index] = base64.b64decode(card["share_b64"], validate=True)
            except (binascii.Error, ValueError):
                presented[index] = b""
        for index, raw in sorted(presented.items()):
            row = rows.get(index)
            bundle = self.orchestrator.load_valid_share(record, row) if row and row["status"] == "STORED" else None
            if bundle is not None and raw and hmac.compare_digest(raw, bundle.to_bytes()):
                genuine[index] = bundle
            else:
                invalid.append(index)
        result: Dict[str, Any] = {
            "rebuilt": False,
            "shares_presented": len(presented),
            "shares_valid": len(genuine),
            "shares_needed": threshold,
            "invalid_shares": invalid,
        }
        if len(genuine) < threshold:
            result["reason"] = f"{len(genuine)} genuine share(s) presented; {threshold} are required"
            return result
        try:
            secret = self.orchestrator.sss_service.recover_secret(list(genuine.values())[:threshold]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise RecoveryError("Valid shares could not be combined") from None
        result.update({"rebuilt": True, "secret": secret})
        return result

    def rebuild(self, record_id: str, owner_id: str) -> Dict[str, Any]:
        record = self._owned_record(record_id, owner_id)
        threshold = record["threshold"]
        held = self._held_shares(record_id)
        indexes = sorted(held)
        if len(indexes) < threshold:
            result: Dict[str, Any] = {
                "rebuilt": False,
                "shares_held": indexes,
                "shares_needed": threshold,
                "reason": f"{len(indexes)} share(s) held; {threshold} are required",
            }
            if len(indexes) == 1:
                result["lone_share_preview"] = self.orchestrator.sss_service.share_preview(held[indexes[0]])
            return result
        sss = self.orchestrator.sss_service
        try:
            secret_bytes = sss.recover_secret([held[i] for i in indexes[:threshold]])
            secret = secret_bytes.decode("utf-8")
            pairs = [
                {"shares": list(combo), "rebuilt": sss.recover_secret([held[i] for i in combo]) == secret_bytes}
                for combo in combinations(indexes, threshold)
            ]
        except (ValueError, UnicodeDecodeError):
            raise RecoveryError("Valid shares could not be combined") from None
        return {"rebuilt": True, "secret": secret, "shares_held": indexes, "shares_needed": threshold, "pairs": pairs}

    def reset(self, record_id: str, owner_id: str) -> None:
        self._owned_record(record_id, owner_id)
        with self._lock:
            self._held.pop(record_id, None)

    def cleanup(self, record_id: str, owner_id: str) -> Dict[str, Any]:
        """Delete this record's shares from every cloud and report what remains."""
        record = self._owned_record(record_id, owner_id)
        self.orchestrator.rollback(record_id)
        with self._lock:
            self._held.pop(record_id, None)
        remaining = 0
        for index, provider_id in enumerate(self.orchestrator.provider_order, start=1):
            try:
                provider = self.orchestrator.provider_registry.get(provider_id)
                if provider.get_share(build_share_id(record_id, record["version"], index)) is not None:
                    remaining += 1
            except Exception:
                remaining += 1  # could not confirm absence
        return {
            "record_id": record_id,
            "status": self.orchestrator.repository.get_record(record_id)["status"],
            "remote_shares_left": remaining,
        }
