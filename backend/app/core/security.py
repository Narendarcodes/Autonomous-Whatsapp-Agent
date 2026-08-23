"""Password hashing and token encryption."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# OWASP-recommended argon2id. Multi-tenant: a leak is high-impact.
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(plain: str) -> str:
    """argon2id hash for dashboard passwords."""
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify; returns False on any mismatch/corruption."""
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the hash params are below current policy (upgrade on login)."""
    try:
        return _ph.check_needs_rehash(hashed)
    except Exception:
        return False


def _get_fernet() -> Fernet:
    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt token (key mismatch or corrupted data)") from exc
