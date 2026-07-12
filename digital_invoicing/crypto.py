"""Token encryption at rest (Phase 16).

FBR bearer tokens = clients ke 5-saala tax credentials — plaintext DB mein
nahi rehne chahiye. Fernet (AES-128-CBC + HMAC), key derivation:
FBR_TOKEN_KEY env var (preferred) warna SECRET_KEY se derived.

Dual-read: decrypt() plaintext legacy values ko waise hi wapas karta hai —
migration se pehle/baad dono safe.
"""
import base64
import hashlib

from django.conf import settings

_PREFIX = "enc$"


def _fernet():
    from cryptography.fernet import Fernet
    raw = getattr(settings, "FBR_TOKEN_KEY", "") or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value or value.startswith(_PREFIX):
        return value or ""
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value            # legacy plaintext — dual-read
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception:
        return ""               # wrong key/corrupt — fail closed
