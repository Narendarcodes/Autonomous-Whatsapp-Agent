import pytest
import pytest_asyncio
import asyncio
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# 1. Modify settings to test DB BEFORE importing app modules
from app.core.config import settings
if not settings.POSTGRES_DB.endswith("_test"):
    settings.POSTGRES_DB = settings.POSTGRES_DB + "_test"

if not getattr(settings, "TOKEN_ENCRYPTION_KEY", None):
    from cryptography.fernet import Fernet
    settings.TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()

# 2. Import Base and database module (will bind to test database)
from app.db.database import Base, AsyncSessionLocal, engine as global_engine
from app.main import app

async def _ensure_test_db_exists():
    default_url = settings.database_url
    # Replace the test DB name with default 'postgres' to run CREATE DATABASE
    base_url, db_name = default_url.rsplit("/", 1)
    admin_url = f"{base_url}/postgres"
    
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        result = await conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"))
        exists = result.scalar()
        if not exists:
            await conn.execute(text(f"CREATE DATABASE {db_name}"))
    await engine.dispose()

@pytest_asyncio.fixture(autouse=True)
async def reset_global_connections():
    """Reset the global Redis client and SQLAlchemy engine pool to avoid loop mismatch errors."""
    from app.db import redis_client
    try:
        await redis_client.close_redis()
    except Exception:
        pass
    try:
        await global_engine.dispose()
    except Exception:
        pass
    yield
    try:
        await redis_client.close_redis()
    except Exception:
        pass
    try:
        await global_engine.dispose()
    except Exception:
        pass

@pytest_asyncio.fixture
async def test_engine():
    await _ensure_test_db_exists()
    
    # Bind to settings.database_url (points to test DB)
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
