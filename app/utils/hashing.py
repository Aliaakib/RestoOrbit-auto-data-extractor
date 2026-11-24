"""Cryptographic hashing utilities for PDF and message deduplication."""

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Compute lowercase hexadecimal SHA-256 digest for raw bytes."""
    return hashlib.sha256(data).hexdigest()
