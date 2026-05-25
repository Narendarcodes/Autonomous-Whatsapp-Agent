"""
Pydantic Schemas for WhatsApp Messages
Request/Response validation
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ==================== WHATSAPP WEBHOOK SCHEMAS ====================

class WhatsAppProfile(BaseModel):
    """WhatsApp user profile"""
    name: str


class WhatsAppContact(BaseModel):
    """WhatsApp contact information"""
    profile: WhatsAppProfile
    wa_id: str  # WhatsApp ID (phone number)


class WhatsAppTextMessage(BaseModel):
    """WhatsApp text message"""
    body: str


class WhatsAppButtonReply(BaseModel):
    """Interactive button reply"""
    id: str
    title: str


class WhatsAppListReply(BaseModel):
    """Interactive list reply"""
    id: str
    title: str
    description: Optional[str] = None


class WhatsAppInteractive(BaseModel):
    """Interactive message data"""
    type: str  # button_reply, list_reply
    button_reply: Optional[WhatsAppButtonReply] = None
    list_reply: Optional[WhatsAppListReply] = None


class WhatsAppMessage(BaseModel):
    """Incoming WhatsApp message"""
    from_: str = Field(alias="from")  # Sender phone number
    id: str  # Message ID
    timestamp: str
    type: str  # text, image, audio, interactive, etc.
    text: Optional[WhatsAppTextMessage] = None
    interactive: Optional[WhatsAppInteractive] = None
    
    class Config:
        populate_by_name = True


class WhatsAppMessageStatus(BaseModel):
    """Message status update"""
    id: str  # Message ID
    status: str  # sent, delivered, read, failed
    timestamp: str
    recipient_id: str
    conversation: Optional[Dict[str, Any]] = None
    pricing: Optional[Dict[str, Any]] = None
    errors: Optional[List[Dict[str, Any]]] = None


class WhatsAppMessageValue(BaseModel):
    """WhatsApp message value - the actual data"""
    messaging_product: str
    metadata: Dict[str, Any]
    contacts: Optional[List[WhatsAppContact]] = None
    messages: Optional[List[WhatsAppMessage]] = None
    statuses: Optional[List[WhatsAppMessageStatus]] = None


class WhatsAppMessageChange(BaseModel):
    """WhatsApp message change event"""
    value: WhatsAppMessageValue
    field: str  # "messages", "message_status", etc.


class WhatsAppEntry(BaseModel):
    """WhatsApp webhook entry"""
    id: str
    changes: List[WhatsAppMessageChange]


class WhatsAppWebhookPayload(BaseModel):
    """Complete WhatsApp webhook payload"""
    object: str
    entry: List[WhatsAppEntry]


# ==================== OUTGOING MESSAGE SCHEMAS ====================

class OutgoingTextMessage(BaseModel):
    """Schema for sending text message to WhatsApp"""
    messaging_product: str = "whatsapp"
    recipient_type: str = "individual"
    to: str  # Recipient phone number
    type: str = "text"
    text: Dict[str, str]  # {"body": "message content"}


# ==================== USER MESSAGE CONTEXT ====================

class MessageContext(BaseModel):
    """Context for processing a user message"""
    user_phone: str
    message_id: str
    message_text: str
    timestamp: datetime
    user_name: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AgentResponse(BaseModel):
    """Response from the agent"""
    message: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    success: bool = True
    error: Optional[str] = None
