from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Iterable, List

import shamirs

SHARE_COUNT = 3
THRESHOLD = 2
CHUNK_SIZE = 15


@dataclass
class ShareBundle:
    index: int
    fragments: List[dict] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        payload = {
            "index": self.index,
            "fragments": [
                {"length": fragment["length"], "share": base64.b64encode(fragment["share"]).decode("ascii")}
                for fragment in self.fragments
            ],
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ShareBundle":
        payload = json.loads(raw.decode("utf-8"))
        index = int(payload["index"])
        if (
            not 1 <= index <= SHARE_COUNT
            or not isinstance(payload["fragments"], list)
            or not payload["fragments"]
        ):
            raise ValueError("Malformed share bundle")
        fragments = [
            {
                "length": int(fragment["length"]),
                "share": base64.b64decode(fragment["share"], validate=True),
            }
            for fragment in payload["fragments"]
        ]
        if any(not 1 <= fragment["length"] <= CHUNK_SIZE for fragment in fragments):
            raise ValueError("Malformed share bundle")
        return cls(index=index, fragments=fragments)


class SSSService:
    def __init__(self, *, share_count: int = SHARE_COUNT, threshold: int = THRESHOLD):
        if (share_count, threshold) != (SHARE_COUNT, THRESHOLD):
            raise ValueError("Firefly requires exactly 3 shares with a threshold of 2")
        self.share_count = share_count
        self.threshold = threshold

    @staticmethod
    def _chunk_payload(payload: bytes, chunk_size: int = CHUNK_SIZE) -> List[bytes]:
        return [payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)]

    @staticmethod
    def decode_share(raw: bytes) -> ShareBundle:
        try:
            return ShareBundle.from_bytes(raw)
        except Exception:
            raise ValueError("Unrecognized share payload") from None

    @staticmethod
    def share_preview(bundle: ShareBundle) -> str:
        """Hex of the first fragment's share value: what a single share looks like (demo display only)."""
        value = shamirs.share.from_bytes(bundle.fragments[0]["share"]).value
        return f"{value:032x}"[:24]

    def split_secret(self, secret: str | bytes) -> List[object]:
        payload = secret.encode("utf-8") if isinstance(secret, str) else secret
        if not payload:
            raise ValueError("Secret must not be empty")

        chunks = self._chunk_payload(payload)
        indexed_fragments = {index: [] for index in range(1, self.share_count + 1)}
        for chunk in chunks:
            chunk_int = int.from_bytes(chunk, byteorder="big", signed=False)
            per_chunk = shamirs.shares(chunk_int, self.share_count, threshold=self.threshold)
            for share in per_chunk:
                indexed_fragments[share.index].append({"length": len(chunk), "share": share.to_bytes()})

        return [ShareBundle(index=index, fragments=indexed_fragments[index]) for index in range(1, self.share_count + 1)]

    def recover_secret(self, shares: Iterable[object]) -> bytes:
        share_list = list(shares)
        if len(share_list) < self.threshold:
            raise ValueError(f"Need at least {self.threshold} valid shares to recover the secret")
        if any(not isinstance(share, ShareBundle) for share in share_list):
            raise ValueError("Unsupported or mixed share formats")
        bundle_list = [share for share in share_list if isinstance(share, ShareBundle)]
        indexes = [share.index for share in bundle_list]
        if len(set(indexes)) != len(indexes):
            raise ValueError("Share indexes must be distinct")
        fragment_counts = {len(share.fragments) for share in bundle_list}
        if len(fragment_counts) != 1 or not fragment_counts or 0 in fragment_counts:
            raise ValueError("Share bundles do not contain matching fragments")

        chunk_positions: dict[int, list[tuple[int, int, object]]] = {}
        for share in bundle_list:
            for chunk_index, fragment in enumerate(share.fragments):
                length = fragment["length"]
                if length <= 0:
                    raise ValueError("Share fragment length is invalid")
                candidate = shamirs.share.from_bytes(fragment["share"])
                if candidate.index != share.index:
                    raise ValueError("Share index does not match its bundle")
                chunk_positions.setdefault(chunk_index, []).append((length, share.index, candidate))

        recovered = bytearray()
        for chunk_index in sorted(chunk_positions):
            related = chunk_positions[chunk_index]
            if len(related) < self.threshold:
                raise ValueError(f"Need at least {self.threshold} valid shares to recover the secret")
            if len({item[0] for item in related}) != 1:
                raise ValueError("Share fragments have inconsistent lengths")
            chunk_length = related[0][0]
            candidates = [item[2] for item in related]
            recovered_int = shamirs.interpolate(candidates[: self.threshold], threshold=self.threshold)
            recovered.extend(recovered_int.to_bytes(chunk_length, byteorder="big"))

        return bytes(recovered)

    def recover_from_share_bytes(self, shares: Iterable[bytes]) -> bytes:
        share_objects = []
        for share in shares:
            share_objects.append(self.decode_share(share))
        return self.recover_secret(share_objects)
