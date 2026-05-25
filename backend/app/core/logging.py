"""
Logging Configuration
Centralized logging setup for the application
"""

import logging
import sys
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
import json


class WebSocketHandler(logging.Handler):
    """Custom handler that broadcasts logs to WebSocket clients"""
    
    def __init__(self):
        super().__init__()
        self.broadcast_queue = asyncio.Queue()
    
    def emit(self, record):
        """Emit log record to WebSocket clients"""
        try:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "message": self.format(record)
            }
            
            # Try to broadcast asynchronously
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._broadcast(log_entry))
            except RuntimeError:
                pass  # No event loop, skip WebSocket broadcast
                
        except Exception:
            self.handleError(record)
    
    async def _broadcast(self, log_entry):
        """Broadcast log entry to all WebSocket clients"""
        try:
            from app.api.logs import manager
            await manager.broadcast(log_entry)
        except Exception:
            pass  # Silently fail if no WebSocket clients


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output"""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record):
        """Format log record with colors"""
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        
        # Format timestamp
        record.asctime = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
        
        # Add color to level name
        record.levelname = f"{log_color}{record.levelname:8}{reset}"
        
        return super().format(record)


class RedisPubSubHandler(logging.Handler):
    """Handler that publishes logs to Redis Pub/Sub"""
    
    def __init__(self):
        super().__init__()
        self.redis = None
        
    async def _publish(self, log_entry):
        try:
            if not self.redis:
                from app.db.redis_client import redis_client
                # Wait for connection if needed
                if not redis_client.client:
                    return
                self.redis = redis_client.client
            
            await self.redis.publish("app_logs", json.dumps(log_entry))
        except Exception:
            pass

    def emit(self, record):
        try:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": record.levelname,
                "message": self.format(record)
            }
            
            # Fire and forget
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._publish(log_entry))
            except RuntimeError:
                pass
        except Exception:
            self.handleError(record)

def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    enable_colors: bool = True
) -> logging.Logger:
    """
    Set up application logging
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path to write logs
        enable_colors: Enable colored output for console
    
    Returns:
        Configured logger instance
    """
    
    # Create logger
    logger = logging.getLogger("whatsapp_calendar_agent")
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    
    if enable_colors:
        console_format = ColoredFormatter(
            fmt='%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    else:
        console_format = logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # WebSocket handler (for local process logs)
    websocket_handler = WebSocketHandler()
    websocket_handler.setLevel(logging.INFO)
    simple_format = logging.Formatter(fmt='%(message)s')
    websocket_handler.setFormatter(simple_format)
    logger.addHandler(websocket_handler)
    
    # Redis Pub/Sub handler (for distributed logs)
    redis_handler = RedisPubSubHandler()
    redis_handler.setLevel(logging.INFO)
    redis_handler.setFormatter(simple_format)
    logger.addHandler(redis_handler)
    
    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        file_format = logging.Formatter(
            fmt='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    
    return logger


# Initialize default logger
from app.core.config import settings

logger = setup_logging(
    log_level=settings.LOG_LEVEL,
    log_file=settings.LOG_FILE,
    enable_colors=True
)


# Log startup info moved to main application startup
