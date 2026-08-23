#!/usr/bin/env python
"""Seed a tenant owner for the omniWA dashboard.

Usage (from backend/, with the venv active):

    python -m scripts.seed_owner --email owner@yourbiz.com \
        --password 'a-strong-password' [--slug mybiz] [--name "My Biz"]

Creates the tenant (if missing) and an is_owner DashboardUser with an argon2
hash. Idempotent: re-running with the same email is a no-op.
"""
import argparse
import asyncio
import sys

MIN_PASSWORD_LEN = 8


async def seed_owner(email: str, password: str, tenant_slug: str, tenant_name: str) -> bool:
    """Create tenant + owner. Returns True if created, False if already exists.

    Raises ValueError on policy violations (weak password, bad email).
    """
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError(f"Invalid email: {email!r}")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if not tenant_slug or not tenant_slug.replace("-", "").isalnum():
        raise ValueError(f"Invalid tenant slug: {tenant_slug!r}")

    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.database import AsyncSessionLocal
    from app.models.models import DashboardUser, Tenant

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(DashboardUser).where(DashboardUser.email == email))
        ).scalar_one_or_none()
        if existing:
            return False

        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name=tenant_name or tenant_slug.title(), slug=tenant_slug)
            db.add(tenant)
            await db.flush()

        user = DashboardUser(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            is_owner=True,
        )
        db.add(user)
        await db.commit()

        # Keep legacy users in the same tenant namespace for isolation
        print(f"Seeded owner {email} -> tenant '{tenant.slug}' (id={tenant.id})")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed an omniWA dashboard owner")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--slug", default="default", help="Tenant slug (default: default)")
    parser.add_argument("--name", default=None, help="Tenant display name")
    args = parser.parse_args()

    try:
        created = asyncio.run(
            seed_owner(args.email, args.password, args.slug, args.name or args.slug.title())
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if created:
        print("Done. Log in at /login with these credentials.")
        return 0
    print("Owner already exists — nothing changed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
