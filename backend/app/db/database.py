"""
PostgreSQL Database Connection
SQLAlchemy setup and session management
"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from typing import Generator, AsyncGenerator
from app.core.config import settings
from app.core.logging import logger


# Create SQLAlchemy engine (Sync)
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,  # Use NullPool for better connection management
    echo=settings.DEBUG,  # Log SQL queries in debug mode
    future=True
)

# Create SQLAlchemy engine (Async)
# Convert postgresql:// to postgresql+asyncpg://
async_database_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
async_engine = create_async_engine(
    async_database_url,
    poolclass=NullPool,
    echo=settings.DEBUG,
    future=True
)


# Create session factory (Sync)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True
)

# Create session factory (Async)
AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


# Base class for all models
Base = declarative_base()


# Database event listeners
@event.listens_for(engine, "connect")
def receive_connect(dbapi_connection, connection_record):
    """Event listener for new database connections"""
    logger.debug("🔌 New database connection established")


@event.listens_for(engine, "close")
def receive_close(dbapi_connection, connection_record):
    """Event listener for closed database connections"""
    logger.debug("🔌 Database connection closed")


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function for FastAPI to get database session (Sync)
    
    Usage:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    
    Yields:
        Database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function for FastAPI to get async database session
    
    Yields:
        Async Database session
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def init_db():
    """
    Initialize database tables
    Creates all tables defined in models
    """
    try:
        logger.info("🗄️  Initializing database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        raise


def check_db_connection() -> bool:
    """
    Check if database connection is working
    
    Returns:
        True if connection successful
    """
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("✅ Database connection successful")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False
