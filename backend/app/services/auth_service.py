"""Dashboard-user credential operations (password rotation)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import hash_password, needs_rehash, verify_password

logger = get_logger(__name__)

MIN_PASSWORD_LEN = 8  # matches scripts/seed_owner.py policy


async def update_password(
    db: AsyncSession,
    user,
    current: str,
    new_pass: str,
) -> bool:
    """Rotate a DashboardUser's password.

    - Verifies `current` against the stored argon2 hash (False on mismatch).
    - Enforces the minimum-length policy on `new_pass` (ValueError).
    - Writes a fresh argon2 hash and commits.

    Returns True when rotated; False when the current password is wrong.
    """
    if len(new_pass) < MIN_PASSWORD_LEN:
        raise ValueError(f"New password must be at least {MIN_PASSWORD_LEN} characters")

    if not verify_password(current, user.password_hash):
        return False

    # No-op guard: identical new password still gets a fresh hash (fresh salt),
    # which is fine — but skip only if hash is current-policy AND same password
    if new_pass == current and not needs_rehash(user.password_hash):
        return True

    user.password_hash = hash_password(new_pass)
    await db.commit()
    logger.info("Password rotated for dashboard user %s", user.email)
    return True
