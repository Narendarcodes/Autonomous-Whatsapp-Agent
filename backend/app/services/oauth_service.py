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
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
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


def build_authorization_url(state: str) -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def exchange_code_for_tokens(code: str) -> Credentials:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    flow.fetch_token(code=code)
    return flow.credentials


async def store_user_credentials(db: AsyncSession, user: User, creds: Credentials) -> None:
    user.google_access_token_enc = encrypt_token(creds.token)
    if creds.refresh_token:
        user.google_refresh_token_enc = encrypt_token(creds.refresh_token)
    user.google_token_expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
    await db.commit()


async def load_user_credentials(user: User) -> Credentials | None:
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
        except Exception as exc:
            logger.error("Token refresh failed for user %s: %s", user.id, exc)
            return None
    return creds


def is_token_near_expiry(user: User) -> bool:
    if not user.google_token_expiry:
        return True
    return user.google_token_expiry - datetime.now(timezone.utc) < timedelta(minutes=5)
