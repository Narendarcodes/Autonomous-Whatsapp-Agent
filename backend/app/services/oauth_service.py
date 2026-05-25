"""
OAuth Service
Handles Google OAuth 2.0 flow for Calendar API access
"""

import asyncio
from functools import partial
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from typing import Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.core.security import generate_oauth_state
from app.models.user import User
from app.db.redis_client import redis_client


class OAuthService:
    """Service for managing Google OAuth 2.0 authentication"""
    
    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI
        self.scopes = settings.GOOGLE_SCOPES
        
    def _create_flow(self) -> Flow:
        """
        Create OAuth flow object
        
        Returns:
            Google OAuth Flow instance
        """
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri],
                }
            },
            scopes=self.scopes,
            redirect_uri=self.redirect_uri
        )
    
    async def generate_authorization_url(
        self,
        user_phone: str,
        db: Session
    ) -> Tuple[str, str]:
        """
        Generate OAuth authorization URL for user
        
        Args:
            user_phone: User's WhatsApp phone number
            db: Database session
            
        Returns:
            Tuple of (authorization_url, state)
        """
        try:
            # Generate state for security
            state = generate_oauth_state()
            
            # Store state in Redis with user_phone mapping
            await redis_client.cache_set(
                f"oauth_state:{state}",
                {"user_phone": user_phone, "timestamp": datetime.utcnow().isoformat()},
                ttl=600  # 10 minutes
            )
            
            # Create OAuth flow
            flow = self._create_flow()
            
            # Generate authorization URL
            authorization_url, _ = flow.authorization_url(
                access_type='offline',  # Get refresh token
                include_granted_scopes='true',
                state=state,
                prompt='consent'  # Force consent to get refresh token
            )
            
            logger.info(f"🔐 Generated OAuth URL for user {user_phone}")
            logger.debug(f"State: {state}")
            
            return authorization_url, state
            
        except Exception as e:
            logger.error(f"Failed to generate authorization URL: {e}")
            raise
    
    async def handle_callback(
        self,
        code: str,
        state: str,
        db
    ) -> Optional[User]:
        """
        Handle OAuth callback and exchange code for tokens
        
        Args:
            code: Authorization code from Google
            state: State parameter for verification
            db: Database session (async)
            
        Returns:
            User object if successful, None otherwise
        """
        try:
            # Verify state and get user_phone
            state_data = await redis_client.cache_get(f"oauth_state:{state}")
            
            if not state_data:
                logger.error(f"Invalid or expired OAuth state: {state}")
                return None
            
            user_phone = state_data.get("user_phone")
            
            if not user_phone:
                logger.error("No user_phone in state data")
                return None
            
            # Delete state from cache
            await redis_client.cache_delete(f"oauth_state:{state}")
            
            # Exchange code for tokens
            flow = self._create_flow()
            flow.fetch_token(code=code)
            
            credentials = flow.credentials
            
            # Get or create user (async)
            from sqlalchemy import select
            query = select(User).where(User.wa_phone == user_phone)
            result = await db.execute(query)
            user = result.scalar_one_or_none()
            
            is_new_user = user is None
            if not user:
                user = User(wa_phone=user_phone)
                db.add(user)
            
            # Store tokens
            user.google_refresh_token = credentials.refresh_token
            user.google_access_token = credentials.token
            user.last_auth_time = datetime.utcnow()
            
            await db.commit()
            await db.refresh(user)
            
            # Cache access token in Redis
            token_expiry = 3600  # 1 hour
            if credentials.expiry:
                token_expiry = int((credentials.expiry - datetime.utcnow()).total_seconds())
            
            await redis_client.cache_oauth_token(
                user_id=user_phone,
                access_token=credentials.token,
                ttl=token_expiry
            )
            
            # Bootstrap proactive schedule for new users or re-authenticated users
            try:
                from app.infrastructure.delayed_scheduler import DelayedJobScheduler
                from app.services.proactive_scheduler import ProactiveScheduler
                
                scheduler = DelayedJobScheduler(redis_client)
                proactive = ProactiveScheduler(scheduler)
                
                scheduled_jobs = await proactive.bootstrap_user_schedule(
                    db=db,
                    user_id=str(user.id)
                )
                
                if scheduled_jobs:
                    total_jobs = sum(len(jobs) for jobs in scheduled_jobs.values())
                    logger.info(f"🚀 Bootstrapped {total_jobs} proactive jobs for user {user_phone}")
            except Exception as bootstrap_error:
                logger.error(f"Failed to bootstrap proactive schedule: {bootstrap_error}")
                # Don't fail OAuth if bootstrap fails
            
            logger.info(f"✅ OAuth successful for user {user_phone}")
            logger.debug(f"Tokens stored, expiry: {token_expiry}s")
            
            return user
            
        except Exception as e:
            logger.error(f"OAuth callback handling failed: {e}")
            await db.rollback()
            raise
    
    async def get_valid_credentials(
        self,
        user: User,
        db: Session
    ) -> Optional[Credentials]:
        """
        Get valid credentials for user, refreshing if necessary
        
        Args:
            user: User object
            db: Database session
            
        Returns:
            Valid Credentials object or None
        """
        try:
            # Check if user has refresh token
            if not user.google_refresh_token:
                logger.warning(f"User {user.wa_phone} has no refresh token")
                return None
            
            # Try to get cached access token
            cached_token = await redis_client.get_oauth_token(user.wa_phone)
            
            if cached_token:
                logger.debug(f"Using cached access token for {user.wa_phone}")
                credentials = Credentials(
                    token=cached_token,
                    refresh_token=user.google_refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    scopes=self.scopes
                )
                return credentials
            
            # Create credentials from stored refresh token
            credentials = Credentials(
                token=user.google_access_token,
                refresh_token=user.google_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=self.scopes
            )
            
            # Check if token is expired
            if not credentials.valid:
                if credentials.expired and credentials.refresh_token:
                    logger.info(f"🔄 Refreshing access token for {user.wa_phone}")
                    
                    # Run the sync refresh in a thread pool to not block event loop
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        partial(credentials.refresh, Request())
                    )
                    
                    # Update database
                    user.google_access_token = credentials.token
                    await db.commit()
                    
                    # Cache new token
                    token_expiry = 3600
                    if credentials.expiry:
                        token_expiry = int((credentials.expiry - datetime.utcnow()).total_seconds())
                    
                    await redis_client.cache_oauth_token(
                        user_id=user.wa_phone,
                        access_token=credentials.token,
                        ttl=token_expiry
                    )
                    
                    logger.info(f"✅ Token refreshed successfully")
                else:
                    logger.error(f"Cannot refresh token for {user.wa_phone}")
                    return None
            
            return credentials
            
        except Exception as e:
            logger.error(f"Failed to get valid credentials: {e}")
            return None
    
    async def revoke_token(
        self,
        user: User,
        db: Session
    ) -> bool:
        """
        Revoke user's OAuth token
        
        Args:
            user: User object
            db: Database session
            
        Returns:
            True if successful
        """
        try:
            if not user.google_refresh_token:
                return True
            
            # Revoke token with Google
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    'https://oauth2.googleapis.com/revoke',
                    params={'token': user.google_refresh_token},
                    headers={'content-type': 'application/x-www-form-urlencoded'}
                )
            
            # Clear tokens from database
            user.google_refresh_token = None
            user.google_access_token = None
            user.last_auth_time = None
            await db.commit()
            
            # Clear cached token
            await redis_client.cache_delete(f"oauth:{user.wa_phone}")
            
            logger.info(f"✅ Token revoked for user {user.wa_phone}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to revoke token: {e}")
            return False
    
    def check_oauth_status(self, user: Optional[User]) -> bool:
        """
        Check if user has valid OAuth setup
        
        Args:
            user: User object
            
        Returns:
            True if user has refresh token
        """
        if not user:
            return False
        
        return bool(user.google_refresh_token)


# Global instance
oauth_service = OAuthService()
