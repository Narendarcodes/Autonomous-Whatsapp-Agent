"""omniWA outbound — the WhatsAppOutbound seam (architecture candidate 2).

Public surface:
    WhatsAppOutbound, DeliveryResult — port types
    OutboundRouter, get_outbound     — production selection + singleton
    adapters                          — Primary / Agent / HermesBridge
"""
from app.outbound.adapters import AgentInstanceAdapter, HermesBridgeAdapter, PrimaryInstanceAdapter
from app.outbound.base import DeliveryResult, WhatsAppOutbound
from app.outbound.router import OutboundRouter, get_outbound, reset_for_tests

__all__ = [
    "AgentInstanceAdapter",
    "DeliveryResult",
    "HermesBridgeAdapter",
    "OutboundRouter",
    "PrimaryInstanceAdapter",
    "WhatsAppOutbound",
    "get_outbound",
    "reset_for_tests",
]
