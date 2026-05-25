"""
OAuth API Endpoints
Handles Google OAuth callback
"""

from fastapi import APIRouter, Request, Query, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.database import get_db
from app.services.oauth_service import oauth_service
from app.services.whatsapp_service import whatsapp_service


router = APIRouter()


@router.get("/oauth/login")
async def oauth_login(
    phone: str = Query(..., description="User's WhatsApp phone number"),
    db: Session = Depends(get_db)
):
    """
    Initiate OAuth login flow
    Redirects user to Google's OAuth consent screen
    
    Args:
        phone: User's WhatsApp phone number
        db: Database session
        
    Returns:
        Redirect to Google OAuth
    """
    try:
        logger.info(f"📝 OAuth login initiated for phone: {phone}")
        
        # Generate authorization URL
        auth_url, state = await oauth_service.generate_authorization_url(phone, db)
        
        logger.info(f"🔗 Redirecting to Google OAuth")
        
        # Redirect to Google OAuth
        return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
        
    except Exception as e:
        logger.error(f"OAuth login error: {e}")
        return HTMLResponse(
            content=f"""
            <html>
                <head><title>OAuth Error</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>❌ OAuth Error</h1>
                    <p>Failed to initiate OAuth login: {str(e)}</p>
                    <p>Please try again or contact support.</p>
                </body>
            </html>
            """,
            status_code=500
        )


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter for security")
):
    """
    OAuth callback endpoint
    Receives authorization code from Google and exchanges for tokens
    
    Args:
        code: Authorization code
        state: State parameter
        
    Returns:
        HTML response with success/failure message
    """
    try:
        logger.info(f"📝 OAuth callback received")
        logger.debug(f"State: {state}, Code: {code[:20]}...")
        
        # Get async database session
        from app.db.database import get_async_session
        async for db in get_async_session():
            try:
                # Handle callback and store tokens
                user = await oauth_service.handle_callback(
                    code=code,
                    state=state,
                    db=db
                )
                
                if not user:
                    logger.error("OAuth callback failed - invalid state or user not found")
                    return HTMLResponse(
                        content=_get_error_html("Authorization failed. Please try again."),
                        status_code=status.HTTP_400_BAD_REQUEST
                    )
                
                # Send success message to user via WhatsApp
                await whatsapp_service.send_oauth_success(user.wa_phone)
                
                logger.info(f"✅ OAuth successful for user {user.wa_phone}")
                
                # Return success page
                return HTMLResponse(content=_get_success_html())
            finally:
                await db.close()
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        return HTMLResponse(
            content=_get_error_html("An error occurred during authorization."),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/oauth/authorize")
async def oauth_authorize(
    phone: str = Query(..., description="User phone number to authorize")
):
    """
    Start OAuth authorization flow
    Generates Google OAuth URL and redirects user
    
    Args:
        phone: User's WhatsApp phone number
        
    Returns:
        Redirect to Google OAuth consent page
    """
    from fastapi.responses import RedirectResponse
    from app.db.database import get_async_session
    
    try:
        logger.info(f"🔐 Starting OAuth flow for phone: {phone}")
        
        # Sanitize phone number
        from app.core.security import sanitize_phone_number
        clean_phone = sanitize_phone_number(phone)
        
        if not clean_phone:
            return HTMLResponse(
                content=_get_error_html("Invalid phone number provided."),
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # Get database session
        async with get_async_session() as db:
            # Generate authorization URL
            auth_url, state = await oauth_service.generate_authorization_url(
                user_phone=clean_phone,
                db=db
            )
            
            logger.info(f"✅ Redirecting to Google OAuth")
            return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
            
    except Exception as e:
        logger.error(f"OAuth authorize error: {e}", exc_info=True)
        return HTMLResponse(
            content=_get_error_html("Failed to start authorization. Please try again."),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get("/oauth/revoke")
async def revoke_oauth(
    phone: str = Query(..., description="User phone number")
):
    """
    Revoke user's OAuth token
    
    Args:
        phone: User phone number
        
    Returns:
        Success/failure response
    """
    try:
        from app.models.user import User
        from app.core.security import sanitize_phone_number
        from app.db.database import get_async_session
        from sqlalchemy import select
        
        phone = sanitize_phone_number(phone)
        
        async for db in get_async_session():
            try:
                query = select(User).where(User.wa_phone == phone)
                result = await db.execute(query)
                user = result.scalar_one_or_none()
                
                if not user:
                    return {"success": False, "error": "User not found"}
                
                success = await oauth_service.revoke_token(user, db)
                
                if success:
                    return {"success": True, "message": "Authorization revoked successfully"}
                else:
                    return {"success": False, "error": "Failed to revoke authorization"}
            finally:
                await db.close()
            
    except Exception as e:
        logger.error(f"OAuth revoke error: {e}")
        return {"success": False, "error": str(e)}


def _get_success_html() -> str:
    """Get success HTML page"""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Authorization Successful</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .container {
                background: white;
                padding: 3rem;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                text-align: center;
                max-width: 500px;
            }
            .success-icon {
                font-size: 5rem;
                margin-bottom: 1rem;
            }
            h1 {
                color: #2d3748;
                margin-bottom: 1rem;
            }
            p {
                color: #4a5568;
                line-height: 1.6;
                margin-bottom: 2rem;
            }
            .whatsapp-icon {
                font-size: 2rem;
                margin-top: 1rem;
            }
            .close-btn {
                background: #667eea;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 25px;
                font-size: 1rem;
                cursor: pointer;
                transition: all 0.3s ease;
            }
            .close-btn:hover {
                background: #764ba2;
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(0,0,0,0.2);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="success-icon">✅</div>
            <h1>Authorization Successful!</h1>
            <p>
                Your Google Calendar is now connected to your WhatsApp AI assistant! 🎉
            </p>
            <p>
                Return to WhatsApp and start managing your calendar by sending messages.
            </p>
            <div class="whatsapp-icon">💬</div>
            <br><br>
            <button class="close-btn" onclick="window.close()">Close This Window</button>
        </div>
    </body>
    </html>
    """


def _get_error_html(message: str) -> str:
    """Get error HTML page"""
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Authorization Failed</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            }}
            .container {{
                background: white;
                padding: 3rem;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                text-align: center;
                max-width: 500px;
            }}
            .error-icon {{
                font-size: 5rem;
                margin-bottom: 1rem;
            }}
            h1 {{
                color: #2d3748;
                margin-bottom: 1rem;
            }}
            p {{
                color: #4a5568;
                line-height: 1.6;
                margin-bottom: 2rem;
            }}
            .close-btn {{
                background: #f5576c;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 25px;
                font-size: 1rem;
                cursor: pointer;
                transition: all 0.3s ease;
            }}
            .close-btn:hover {{
                background: #f093fb;
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(0,0,0,0.2);
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="error-icon">❌</div>
            <h1>Authorization Failed</h1>
            <p>{message}</p>
            <p>Please return to WhatsApp and try again.</p>
            <button class="close-btn" onclick="window.close()">Close This Window</button>
        </div>
    </body>
    </html>
    """
