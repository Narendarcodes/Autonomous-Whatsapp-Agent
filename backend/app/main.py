"""
FastAPI Main Application
WhatsApp AI Calendar Agent Backend
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import sys

from app.core.config import settings
from app.core.logging import logger
from app.db.database import check_db_connection, init_db
from app.db.redis_client import redis_client
from app.api import webhooks, health, oauth, logs
import asyncio
import json

async def subscribe_to_logs():
    """Subscribe to Redis logs and broadcast to WebSockets"""
    try:
        # Wait for Redis connection
        while not redis_client.client:
            await asyncio.sleep(1)
            
        pubsub = redis_client.client.pubsub()
        await pubsub.subscribe("app_logs")
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    log_entry = json.loads(message["data"])
                    from app.api.logs import manager
                    await manager.broadcast(log_entry)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Log subscription error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager
    Handles startup and shutdown events
    """
    # ==================== STARTUP ====================
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 60)
    
    try:
        # Check Python version
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        logger.info(f"🐍 Python version: {python_version}")
        
        # SECURITY: Warn if DEBUG mode is on in production
        if settings.DEBUG and settings.ENVIRONMENT.lower() == "production":
            logger.warning("⚠️ SECURITY WARNING: DEBUG mode is enabled in PRODUCTION environment!")
            logger.warning("⚠️ Set DEBUG=False for production deployments.")
        
        # Connect to Redis
        logger.info("📡 Connecting to Redis...")
        await redis_client.connect()
        
        # Check database connection
        logger.info("🗄️  Checking database connection...")
        if check_db_connection():
            logger.info("✅ Database connection successful")
            
            # Initialize database tables (only in DEBUG mode with non-production environment)
            if settings.DEBUG and settings.ENVIRONMENT.lower() != "production":
                init_db()
        else:
            logger.error("❌ Database connection failed")
            raise Exception("Database connection failed")
        
        # Check LLM connection
        if settings.USE_GITHUB_MODELS:
            logger.info(f"🤖 LLM: GitHub Models ({settings.GITHUB_MODEL})")
        else:
            logger.warning(f"⚠️ GitHub Models disabled - no LLM configured")
        
        # Start log subscription
        asyncio.create_task(subscribe_to_logs())
        
        logger.info("=" * 60)
        logger.info("✅ Application startup complete")
        logger.info(f"📝 Environment: {settings.ENVIRONMENT}")
        logger.info(f"🐛 Debug mode: {settings.DEBUG}")
        logger.info(f"🌐 Listening on: http://0.0.0.0:8000")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise
    
    yield
    
    # ==================== SHUTDOWN ====================
    logger.info("=" * 60)
    logger.info("🛑 Shutting down application...")
    logger.info("=" * 60)
    
    try:
        # Disconnect from Redis
        logger.info("📡 Disconnecting from Redis...")
        await redis_client.disconnect()
        
        logger.info("✅ Shutdown complete")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {e}")
    
    logger.info("=" * 60)


# ==================== CREATE APP ====================

# SECURITY: Disable docs in production even if DEBUG is accidentally set
is_production = settings.ENVIRONMENT.lower() == "production"
enable_docs = settings.DEBUG and not is_production

app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered WhatsApp bot for Google Calendar management using GitHub Models GPT-4o-mini",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if enable_docs else None,
    redoc_url="/redoc" if enable_docs else None,
)


# ==================== MIDDLEWARE ====================

# CORS middleware - SECURITY: Restrict origins in production
cors_origins = ["*"] if (settings.DEBUG and not is_production) else []
if is_production and hasattr(settings, 'ALLOWED_ORIGINS'):
    cors_origins = settings.ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True if cors_origins else False,
    allow_methods=["GET", "POST", "PUT", "DELETE"] if is_production else ["*"],
    allow_headers=["*"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests"""
    logger.info(f"📨 {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        logger.info(f"✅ {request.method} {request.url.path} - {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"❌ {request.method} {request.url.path} - Error: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error"}
        )


# ==================== EXCEPTION HANDLERS ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "detail": str(exc) if settings.DEBUG else "An error occurred"
        }
    )


# ==================== INCLUDE ROUTERS ====================

app.include_router(health.router, tags=["Health"])
app.include_router(webhooks.router, tags=["WhatsApp Webhooks"])
app.include_router(oauth.router, tags=["OAuth"])
app.include_router(logs.router, tags=["Logs"])


# ==================== ROOT ENDPOINT ====================

@app.get("/", response_class=PlainTextResponse)
async def root():
    """Root endpoint"""
    return f"""
    {settings.APP_NAME} v{settings.APP_VERSION}
    
    Status: Running
    Environment: {settings.ENVIRONMENT}
    
    Endpoints:
    - GET  /health         - Health check
    - GET  /webhook        - WhatsApp webhook verification
    - POST /webhook        - WhatsApp webhook receiver
    - GET  /docs           - API documentation (debug mode only)
    """


# ==================== STARTUP MESSAGE ====================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
