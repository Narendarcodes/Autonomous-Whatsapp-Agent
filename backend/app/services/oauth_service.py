"""Google OAuth service for Calendar (and future Google APIs)."""
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_token, encrypt_token
from app.models.models import User

logger = get_logger(__name__)

SCOPES = [
    # Calendar (Phase 1)
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    # Drive & Docs & Sheets (Phase 2 - "Endless Possibilities")
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    # Gmail (Phase 3)
    "https://www.googleapis.com/auth/gmail.modify",
]


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }


def build_authorization_url(state: str) -> tuple[str, str]:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url, flow.code_verifier


def exchange_code_for_tokens(code: str, code_verifier: str | None = None) -> Credentials:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


def sync_credentials_to_hermes(creds: Credentials) -> None:
    """Sync Google OAuth tokens to the Hermes container shared volume."""
    import json
    from pathlib import Path
    import os

    hermes_dir = Path("/opt/hermes_data")
    if not (hermes_dir.exists() and hermes_dir.is_dir()):
        logger.debug("Hermes data directory /opt/hermes_data not found. Skipping sync.")
        return

    try:
        scopes = list(creds.scopes) if creds.scopes else SCOPES
        # 1. Write google_token.json
        token_payload = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": creds.refresh_token,
            "type": "authorized_user",
            "scopes": scopes
        }
        token_path = hermes_dir / "google_token.json"
        token_path.write_text(json.dumps(token_payload, indent=2))

        # 2. Write google_client_secret.json
        secret_payload = {
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "project_id": "whatsapp-agent-calendar",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uris": [settings.GOOGLE_REDIRECT_URI]
            }
        }
        secret_path = hermes_dir / "google_client_secret.json"
        secret_path.write_text(json.dumps(secret_payload, indent=2))

        # 3. Change ownership to hermes user inside the container (uid 1000, gid 1000)
        try:
            os.chown(str(token_path), 1000, 1000)
            os.chown(str(secret_path), 1000, 1000)
        except Exception as chown_exc:
            logger.warning("Could not set chown for token files in shared volume: %s", chown_exc)

        logger.info("Successfully synchronized Google credentials to Hermes shared volume.")
    except Exception as e:
        logger.error("Failed to sync Google credentials to Hermes: %s", e)


async def store_user_credentials(db: AsyncSession, user: User, creds: Credentials) -> None:
    user.google_access_token_enc = encrypt_token(creds.token)
    if creds.refresh_token:
        user.google_refresh_token_enc = encrypt_token(creds.refresh_token)
    user.google_token_expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
    await db.commit()
    sync_credentials_to_hermes(creds)


async def load_user_credentials(user: User, db: AsyncSession | None = None) -> Credentials | None:
    if not user.google_access_token_enc:
        return None

    creds = Credentials(
        token=decrypt_token(user.google_access_token_enc),
        refresh_token=(
            decrypt_token(user.google_refresh_token_enc) if user.google_refresh_token_enc else None
        ),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    creds.expiry = user.google_token_expiry.replace(tzinfo=None) if user.google_token_expiry else None

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            logger.info("Refreshed Google token for user %s", user.id)
            if db:
                user.google_access_token_enc = encrypt_token(creds.token)
                if creds.refresh_token:
                    user.google_refresh_token_enc = encrypt_token(creds.refresh_token)
                user.google_token_expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
                await db.commit()
                logger.info("Saved refreshed Google token to database for user %s", user.id)
            sync_credentials_to_hermes(creds)
        except Exception as exc:
            logger.error("Token refresh failed for user %s: %s", user.id, exc)
            return None
    return creds


def is_token_near_expiry(user: User) -> bool:
    if not user.google_token_expiry:
        return True
    return user.google_token_expiry - datetime.now(timezone.utc) < timedelta(minutes=5)
