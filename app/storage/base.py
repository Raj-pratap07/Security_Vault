from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ShareEnvelope:
    record_id: str
    version: int
    share_index: int
    provider_id: str
    key_ref: str
    format_version: int
    ciphertext: str


@dataclass
class PutResult:
    provider_id: str
    share_id: str
    status: str
    success: bool
    detail: Optional[str] = None


@dataclass
class DeleteResult:
    provider_id: str
    share_id: str
    status: str
    deleted: bool
    existed: bool


@dataclass
class HealthResult:
    provider_id: str
    status: str
    ok: bool


class BaseStorageAdapter(ABC):
    provider_id: str

    @abstractmethod
    def put_share(self, share_id: str, envelope: ShareEnvelope) -> PutResult:
        raise NotImplementedError

    @abstractmethod
    def get_share(self, share_id: str) -> Optional[ShareEnvelope]:
        raise NotImplementedError

    @abstractmethod
    def delete_share(self, share_id: str) -> DeleteResult:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> HealthResult:
        raise NotImplementedError

    def close(self) -> None:
        """Release client resources; adapters without long-lived clients inherit this no-op."""


def build_share_id(record_id: str, version: int, share_index: int) -> str:
    return f"firefly:{record_id}:v{version}:share:{share_index}"
