"""Connector Service — manages credentials for integrations like Notion, Linear, GitHub, and Airtable."""
import os
import json
from pathlib import Path
from dotenv import set_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import AsyncSessionLocal
from app.models.models import User, UserPreference
from app.core.security import encrypt_token, decrypt_token
from app.services.preferences_service import preferences_service
from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_CONNECTORS = {
    "notion": ["api_key"],
    "linear": ["api_key"],
    "github": ["token"],
    "airtable": ["api_key"],
    "smtp": ["host", "port", "username", "password"],
}

ENV_KEY_MAP = {
    "notion:api_key": "NOTION_API_KEY",
    "linear:api_key": "LINEAR_API_KEY",
    "github:token": "GITHUB_TOKEN",
    "airtable:api_key": "AIRTABLE_API_KEY",
    "smtp:host": "EMAIL_SMTP_HOST",
    "smtp:port": "EMAIL_SMTP_PORT",
    "smtp:username": "EMAIL_SMTP_USER",
    "smtp:password": "EMAIL_SMTP_PASS",
}

class ConnectorService:
    async def save_credentials(self, user_id: str, connector: str, data: dict) -> None:
        """Save encrypted connector credentials to UserPreference."""
        if connector not in SUPPORTED_CONNECTORS:
            raise ValueError(f"Unsupported connector: {connector}")

        for field in SUPPORTED_CONNECTORS[connector]:
            val = data.get(field)
            if val is not None:
                key = f"connector:{connector}:{field}"
                # Encrypt sensitive tokens/passwords
                is_sensitive = field in ("api_key", "token", "password")
                db_val = encrypt_token(str(val)) if is_sensitive else str(val)
                await preferences_service.set(user_id, key, db_val, source="explicit")

        # After saving, trigger sync to Hermes environment
        await self.sync_to_hermes(user_id)

    async def get_credentials(self, user_id: str, connector: str) -> dict:
        """Get decrypted connector credentials from UserPreference."""
        if connector not in SUPPORTED_CONNECTORS:
            raise ValueError(f"Unsupported connector: {connector}")

        result = {}
        for field in SUPPORTED_CONNECTORS[connector]:
            key = f"connector:{connector}:{field}"
            db_val = await preferences_service.get(user_id, key)
            if db_val is not None:
                is_sensitive = field in ("api_key", "token", "password")
                try:
                    result[field] = decrypt_token(db_val) if is_sensitive else db_val
                except Exception:
                    # Fallback in case it wasn't encrypted or failed decryption
                    result[field] = db_val
            else:
                result[field] = ""
        return result

    async def sync_to_hermes(self, user_id: str) -> None:
        """Read all saved connectors and write them as environment variables in the shared volume .env."""
        hermes_dir = Path("/opt/hermes_data")
        if not (hermes_dir.exists() and hermes_dir.is_dir()):
            logger.debug("Hermes data directory /opt/hermes_data not found. Skipping env sync.")
            return

        dotenv_path = hermes_dir / ".env"
        # Ensure .env file exists
        if not dotenv_path.exists():
            dotenv_path.touch()

        # Load all user preferences
        prefs = await preferences_service.get_all(user_id)
        
        for pref_key, db_val in prefs.items():
            if pref_key.startswith("connector:"):
                # Parse connector name and field
                # E.g. connector:notion:api_key
                parts = pref_key.split(":")
                if len(parts) >= 3:
                    connector = parts[1]
                    field = parts[2]
                    
                    env_key = ENV_KEY_MAP.get(f"{connector}:{field}")
                    if env_key:
                        is_sensitive = field in ("api_key", "token", "password")
                        try:
                            val = decrypt_token(db_val) if is_sensitive else db_val
                        except Exception:
                            val = db_val
                        
                        # Set the environment variable in Hermes shared .env
                        try:
                            set_key(str(dotenv_path), env_key, val)
                            logger.debug(f"Synced {env_key} to Hermes shared .env")
                        except Exception as e:
                            logger.error(f"Failed to write {env_key} to .env: {e}")

        # Set permission/owner of the .env file
        try:
            os.chown(str(dotenv_path), 1000, 1000)
        except Exception:
            pass

    async def list_connectors_status(self, user_id: str) -> list[dict]:
        """Return connection status and configured metadata for all connectors."""
        connectors = []
        
        # Check Google Workspace OAuth
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            google_connected = user is not None and user.google_access_token_enc is not None
            
        connectors.append({
            "id": "google-workspace",
            "name": "Google Workspace",
            "description": "Access Gmail, Calendar, Google Drive, Sheets, and Docs",
            "connected": google_connected,
            "type": "oauth",
        })

        for conn, fields in SUPPORTED_CONNECTORS.items():
            # Skip smtp since it is email configuration
            if conn == "smtp":
                continue
            
            creds = await self.get_credentials(user_id, conn)
            # A connector is connected if its main credentials field is not empty
            main_field = fields[0]
            connected = bool(creds.get(main_field))
            
            name_map = {
                "notion": "Notion",
                "linear": "Linear",
                "github": "GitHub",
                "airtable": "Airtable",
            }
            desc_map = {
                "notion": "Sync pages, databases, and workspace notes",
                "linear": "Track team tickets, boards, and software tasks",
                "github": "Inspect repository files, issues, and pull requests",
                "airtable": "Log records, metrics, and tabular databases",
            }
            
            connectors.append({
                "id": conn,
                "name": name_map.get(conn, conn.title()),
                "description": desc_map.get(conn, ""),
                "connected": connected,
                "type": "token",
            })
            
        # Add email
        smtp_creds = await self.get_credentials(user_id, "smtp")
        email_connected = bool(smtp_creds.get("host") and smtp_creds.get("username"))
        connectors.append({
            "id": "email",
            "name": "Email (IMAP/SMTP)",
            "description": "Send and receive email via standard mail servers",
            "connected": email_connected,
            "type": "credentials",
        })

        return connectors

connector_service = ConnectorService()
