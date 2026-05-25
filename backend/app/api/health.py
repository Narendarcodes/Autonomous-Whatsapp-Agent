"""
Health Check Endpoints
System status and diagnostics
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from datetime import datetime
import asyncio

from app.core.config import settings
from app.core.logging import logger
from app.db.redis_client import redis_client
from app.db.database import check_db_connection
from app.services.llm_factory import llm_service


router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """
    Basic health check endpoint
    Returns 200 if service is running
    """
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/health/detailed", status_code=status.HTTP_200_OK)
async def detailed_health_check():
    """
    Detailed health check with dependency status
    Checks database, Redis, and LLM connectivity
    """
    health_status = {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {}
    }
    
    all_healthy = True
    
    # Check PostgreSQL
    try:
        db_healthy = check_db_connection()
        health_status["dependencies"]["database"] = {
            "status": "healthy" if db_healthy else "unhealthy",
            "type": "postgresql",
            "host": settings.POSTGRES_HOST
        }
        if not db_healthy:
            all_healthy = False
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["dependencies"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        all_healthy = False
    
    # Check Redis
    try:
        redis_healthy = await redis_client.ping()
        health_status["dependencies"]["redis"] = {
            "status": "healthy" if redis_healthy else "unhealthy",
            "type": "redis",
            "host": settings.REDIS_HOST
        }
        if not redis_healthy:
            all_healthy = False
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        health_status["dependencies"]["redis"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        all_healthy = False
    
    # Check LLM Service
    try:
        llm_healthy = await llm_service.health_check()
        
        # Determine provider type
        provider_type = "github_models" if settings.USE_GITHUB_MODELS else "ollama"
        provider_name = "GitHub Models" if settings.USE_GITHUB_MODELS else "Ollama"
        model_name = settings.GITHUB_MODEL if settings.USE_GITHUB_MODELS else "Local LLM"
        
        health_status["dependencies"]["llm"] = {
            "status": "healthy" if llm_healthy else "unhealthy",
            "type": provider_type,
            "provider": provider_name,
            "model": model_name
        }
        
        if not llm_healthy:
            all_healthy = False
    except Exception as e:
        logger.error(f"LLM health check failed: {e}")
        health_status["dependencies"]["llm"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        all_healthy = False
    
    # Update overall status
    health_status["status"] = "healthy" if all_healthy else "degraded"
    
    # Return appropriate status code
    status_code = status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    
    return JSONResponse(status_code=status_code, content=health_status)


@router.get("/health/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    """
    Readiness check for Kubernetes/Docker health checks
    Returns 200 only if all critical services are available
    """
    try:
        # Check critical services
        db_ready = check_db_connection()
        redis_ready = await redis_client.ping()
        
        if db_ready and redis_ready:
            return {"status": "ready"}
        else:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not ready", "reason": "Dependencies unavailable"}
            )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not ready", "error": str(e)}
        )


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness_check():
    """
    Liveness check for Kubernetes/Docker
    Returns 200 if the application process is running
    """
    return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}


@router.post("/test/proactive/bootstrap/{phone}")
async def test_bootstrap_proactive(phone: str):
    """
    Test endpoint to bootstrap proactive schedule for a user
    Only available in development mode
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "Only available in debug mode"}
        )
    
    try:
        from app.db.database import get_async_session
        from app.models.user import User
        from app.infrastructure.delayed_scheduler import DelayedJobScheduler
        from app.services.proactive_scheduler import ProactiveScheduler
        from sqlalchemy import select
        
        async for db in get_async_session():
            try:
                # Find user by phone
                query = select(User).where(User.wa_phone == phone)
                result = await db.execute(query)
                user = result.scalar_one_or_none()
                
                if not user:
                    return JSONResponse(
                        status_code=status.HTTP_404_NOT_FOUND,
                        content={"error": f"User with phone {phone} not found"}
                    )
                
                # Bootstrap proactive schedule
                scheduler = DelayedJobScheduler(redis_client)
                proactive = ProactiveScheduler(scheduler)
                
                scheduled_jobs = await proactive.bootstrap_user_schedule(
                    db=db,
                    user_id=str(user.id)
                )
                
                total_jobs = sum(len(jobs) for jobs in scheduled_jobs.values())
                
                return {
                    "success": True,
                    "user_phone": phone,
                    "user_id": str(user.id),
                    "total_jobs_scheduled": total_jobs,
                    "jobs": {k: len(v) for k, v in scheduled_jobs.items()}
                }
            finally:
                await db.close()
                
    except Exception as e:
        logger.error(f"Failed to bootstrap proactive schedule: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )


@router.post("/test/proactive/reminder")
async def test_schedule_reminder():
    """
    Test endpoint to schedule a reminder job in 1 minute
    Only available in development mode
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "Only available in debug mode"}
        )
    
    try:
        from datetime import timedelta
        from uuid import uuid4
        from app.infrastructure.delayed_scheduler import DelayedJobScheduler
        
        scheduler = DelayedJobScheduler(redis_client)
        
        # Schedule a test job for 1 minute from now
        job_id = f"test_reminder_{uuid4().hex[:8]}"
        scheduled_time = datetime.utcnow() + timedelta(minutes=1)
        
        # Get first user from database for testing
        from app.db.database import get_async_session
        from app.models.user import User
        from sqlalchemy import select
        
        async for db in get_async_session():
            try:
                query = select(User).limit(1)
                result = await db.execute(query)
                user = result.scalar_one_or_none()
                
                if not user:
                    return JSONResponse(
                        status_code=status.HTTP_404_NOT_FOUND,
                        content={"error": "No users found in database"}
                    )
                
                success = await scheduler.schedule_job(
                    job_id=job_id,
                    job_type="event_reminder",
                    user_id=str(user.id),
                    payload={
                        "event_id": "test_event",
                        "event_summary": "🧪 Test Reminder - System Working!",
                        "event_start_time": (datetime.utcnow() + timedelta(minutes=16)).isoformat(),
                        "reminder_type": "15min"
                    },
                    scheduled_time=scheduled_time
                )
                
                if success:
                    return {
                        "success": True,
                        "job_id": job_id,
                        "scheduled_for": scheduled_time.isoformat(),
                        "will_notify_phone": user.wa_phone,
                        "message": "Test reminder scheduled for 1 minute from now"
                    }
                else:
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={"error": "Failed to schedule job"}
                    )
            finally:
                await db.close()
                
    except Exception as e:
        logger.error(f"Failed to schedule test reminder: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )


@router.get("/test/proactive/jobs")
async def test_list_scheduled_jobs():
    """
    List all scheduled proactive jobs
    Only available in development mode
    """
    if not settings.DEBUG:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "Only available in debug mode"}
        )
    
    try:
        from app.infrastructure.delayed_scheduler import DelayedJobScheduler
        
        scheduler = DelayedJobScheduler(redis_client)
        
        # Get all pending jobs (using correct key: delayed_jobs)
        pending_count = await redis_client.client.zcard("delayed_jobs")
        processing_count = await redis_client.client.scard("delayed_jobs:processing")
        
        # Get next 10 jobs
        next_jobs_raw = await redis_client.client.zrange(
            "delayed_jobs", 0, 9, withscores=True
        )
        
        next_jobs = []
        for job_json, score in next_jobs_raw:
            import json
            try:
                job = json.loads(job_json)
                job["scheduled_time_unix"] = score
                job["scheduled_time_human"] = datetime.fromtimestamp(score).isoformat()
                next_jobs.append(job)
            except:
                pass
        
        return {
            "pending_jobs": pending_count,
            "processing_jobs": processing_count,
            "next_10_jobs": next_jobs
        }
        
    except Exception as e:
        logger.error(f"Failed to list scheduled jobs: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(e)}
        )

