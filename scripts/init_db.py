"""Explicit first-time creation of the encrypted metadata database.

Normal application startup never creates the database. Run this once, with the
SQLCIPHER_KEY_B64 you intend to keep; it refuses to touch an existing file.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.errors import ConfigurationError
from app.db.repository import SecretRepository


def main() -> int:
    try:
        settings = get_settings()
        SecretRepository.initialize(
            settings.database_path,
            settings.sqlcipher_key,
            share_count=settings.share_count,
            threshold=settings.threshold,
        )
    except (ConfigurationError, ValueError) as exc:
        print(f"Metadata database initialization: FAILED ({exc})")
        return 1
    print(f"Metadata database initialized: {settings.database_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
