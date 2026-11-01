from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class SecretCreateRequest(BaseModel):
    secret: str = Field(..., min_length=1, max_length=4096)

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(value.encode("utf-8")) == 0:
            raise ValueError("Secret must not be empty")
        return value


class SecretCreateResponse(BaseModel):
    record_id: str
    status: str
    share_count: int = 3
    threshold: int = 2
    created_at: str


class SecretRecoverResponse(BaseModel):
    record_id: str
    secret: str


class SecretStatusResponse(BaseModel):
    record_id: str
    version: int
    status: str
    threshold: int
    share_count: int
    confirmed_shares: int
    created_at: str
    updated_at: str
    operation_id: Optional[str] = None


class DemoShareInfo(BaseModel):
    share_index: int
    provider_id: str
    status: str
    ciphertext_preview: Optional[str] = None


class DemoCollectResponse(BaseModel):
    share_index: int
    provider_id: str
    fingerprint: str
    latency_ms: float
    shares_held: List[int]


class DemoPairResult(BaseModel):
    shares: List[int]
    rebuilt: bool


class DemoRebuildResponse(BaseModel):
    rebuilt: bool
    shares_held: List[int]
    shares_needed: int
    secret: Optional[str] = None
    reason: Optional[str] = None
    lone_share_preview: Optional[str] = None
    pairs: Optional[List[DemoPairResult]] = None


class DemoShareCard(BaseModel):
    record_id: str
    share_index: int
    threshold: int
    share_count: int
    share_b64: str


class DemoFetchCard(BaseModel):
    share_index: int = Field(..., ge=1, le=3)
    share_b64: str = Field(..., max_length=200_000)


class DemoFetchRequest(BaseModel):
    cards: List[DemoFetchCard] = Field(..., min_length=1, max_length=3)


class DemoFetchResponse(BaseModel):
    rebuilt: bool
    shares_presented: int
    shares_valid: int
    shares_needed: int
    invalid_shares: List[int] = []
    secret: Optional[str] = None
    reason: Optional[str] = None


class DemoCleanupResponse(BaseModel):
    record_id: str
    status: str
    remote_shares_left: int

