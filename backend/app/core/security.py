"""
Security utilities
Token validation, encryption, and other security functions
"""

import hashlib
import secrets
import hmac
from typing import Optional
from datetime import datetime, timedelta
from app.core.logging import logger


def generate_token(length: int = 32) -> str:
    """
    Generate a secure random token
    
    Args:
        length: Length of the token
        
    Returns:
        Hexadecimal token string
    """
    return secrets.token_hex(length)


def hash_string(value: str, salt: Optional[str] = None) -> str:
    """
    Hash a string using SHA-256
    
    Args:
        value: String to hash
        salt: Optional salt value
        
    Returns:
        Hexadecimal hash string
    """
    if salt:
        value = f"{value}{salt}"
    return hashlib.sha256(value.encode()).hexdigest()


def verify_whatsapp_signature(payload: str, signature: str, secret: str) -> bool:
    """
    Verify WhatsApp webhook signature
    
    Args:
        payload: Request payload (body)
        signature: X-Hub-Signature-256 header value
        secret: App secret from Meta
        
    Returns:
        True if signature is valid
    """
    try:
        # Remove 'sha256=' prefix if present
        if signature.startswith('sha256='):
            signature = signature[7:]
        
        # Calculate expected signature
        expected_signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures securely
        return hmac.compare_digest(expected_signature, signature)
    
    except Exception as e:
        logger.error(f"Signature verification failed: {e}")
        return False


def sanitize_phone_number(phone: str) -> str:
    """
    Sanitize and normalize phone number
    
    Args:
        phone: Phone number string
        
    Returns:
        Normalized phone number (digits only)
    """
    # Remove all non-digit characters
    return ''.join(filter(str.isdigit, phone))


def mask_sensitive_data(data: str, visible_chars: int = 4) -> str:
    """
    Mask sensitive data for logging
    
    Args:
        data: Sensitive string to mask
        visible_chars: Number of characters to keep visible
        
    Returns:
        Masked string
    """
    if len(data) <= visible_chars:
        return '*' * len(data)
    
    return data[:visible_chars] + '*' * (len(data) - visible_chars)


def validate_token_expiry(token_created_at: datetime, ttl: int) -> bool:
    """
    Check if a token has expired
    
    Args:
        token_created_at: Token creation timestamp
        ttl: Time-to-live in seconds
        
    Returns:
        True if token is still valid
    """
    expiry_time = token_created_at + timedelta(seconds=ttl)
    return datetime.utcnow() < expiry_time


def generate_oauth_state() -> str:
    """
    Generate a secure state parameter for OAuth flow
    
    Returns:
        Random state string
    """
    return generate_token(16)
