"""
Redis Sorted Set Delayed Job Scheduler
Handles proactive notifications and reminders
"""

import json
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from uuid import uuid4

from redis.asyncio import Redis

from app.core.logging import logger


class DelayedJobScheduler:
    """Scheduler using Redis Sorted Sets for delayed job execution"""
    
    def __init__(self, redis_client: Any):
        """
        Initialize scheduler
        
        Args:
            redis_client: Redis connection instance or RedisClient wrapper
        """
        self.redis_client = redis_client
        self.sorted_set_key = "delayed_jobs"
        self.processing_set_key = "delayed_jobs:processing"

    @property
    def client(self) -> Redis:
        """Get the underlying Redis client"""
        if hasattr(self.redis_client, 'client'):
            return self.redis_client.client
        return self.redis_client
    
    async def schedule_job(
        self,
        job_id: str,
        job_type: str,
        user_id: str,
        payload: Dict[str, Any],
        scheduled_time: datetime
    ) -> bool:
        """
        Schedule a job for future execution
        
        Args:
            job_id: Unique job identifier
            job_type: Type of job (reminder, morning_briefing, etc.)
            user_id: User UUID
            payload: Job-specific data
            scheduled_time: When to execute the job
            
        Returns:
            True if scheduled successfully
        """
        try:
            # Score is Unix timestamp for scheduled time
            score = scheduled_time.timestamp()
            
            # Value is JSON with job metadata
            job_data = {
                "job_id": job_id,
                "job_type": job_type,
                "user_id": user_id,
                "payload": payload,
                "scheduled_time": scheduled_time.isoformat(),
                "created_at": datetime.utcnow().isoformat()
            }
            
            # ZADD adds to sorted set (score, member)
            result = await self.client.zadd(
                self.sorted_set_key,
                {json.dumps(job_data): score}
            )
            
            if result:
                logger.info(f"📅 Scheduled job: {job_type} (id={job_id}) at {scheduled_time}")
            else:
                logger.warning(f"⚠️ Job {job_id} already exists, not rescheduled")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"Failed to schedule job {job_id}: {e}")
            return False
    
    async def get_due_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get jobs that are due for execution (score <= current time)
        
        Args:
            limit: Maximum jobs to retrieve
            
        Returns:
            List of job dictionaries
        """
        try:
            current_time = time.time()
            
            # ZRANGEBYSCORE returns members with score in range
            # min=0, max=current_time (all jobs due)
            results = await self.client.zrangebyscore(
                self.sorted_set_key,
                min=0,
                max=current_time,
                start=0,
                num=limit,
                withscores=True
            )
            
            if not results:
                return []
            
            jobs = []
            for job_json, score in results:
                try:
                    job_data = json.loads(job_json)
                    job_data['score'] = score
                    jobs.append(job_data)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse job data: {e}")
                    continue
            
            if jobs:
                logger.debug(f"⏰ Found {len(jobs)} due job(s)")
            
            return jobs
            
        except Exception as e:
            logger.error(f"Failed to get due jobs: {e}")
            return []
    
    async def mark_job_processing(self, job_id: str, job_json: str) -> bool:
        """
        Move job to processing set (prevents duplicate execution)
        
        Args:
            job_id: Job identifier
            job_json: Job JSON string from sorted set
            
        Returns:
            True if marked successfully
        """
        try:
            # Atomic operation: remove from main set, add to processing set
            pipeline = self.client.pipeline()
            
            # Remove from delayed_jobs
            pipeline.zrem(self.sorted_set_key, job_json)
            
            # Add to processing set with current timestamp
            pipeline.zadd(
                self.processing_set_key,
                {job_json: time.time()}
            )
            
            results = await pipeline.execute()
            
            if results[0]:  # Successfully removed from main set
                logger.debug(f"🔄 Marked job {job_id} as processing")
                return True
            else:
                logger.warning(f"⚠️ Job {job_id} already processed")
                return False
            
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as processing: {e}")
            return False
    
    async def complete_job(self, job_json: str) -> bool:
        """
        Remove job from processing set after successful execution
        
        Args:
            job_json: Job JSON string
            
        Returns:
            True if removed successfully
        """
        try:
            result = await self.client.zrem(self.processing_set_key, job_json)
            
            if result:
                logger.debug(f"✅ Completed job processing")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"Failed to complete job: {e}")
            return False
    
    async def reschedule_job(self, job_json: str, new_scheduled_time: datetime) -> bool:
        """
        Reschedule a job (e.g., after failure)
        
        Args:
            job_json: Job JSON string
            new_scheduled_time: New execution time
            
        Returns:
            True if rescheduled successfully
        """
        try:
            # Remove from processing
            await self.client.zrem(self.processing_set_key, job_json)
            
            # Add back to main set with new score
            score = new_scheduled_time.timestamp()
            result = await self.client.zadd(
                self.sorted_set_key,
                {job_json: score}
            )
            
            if result:
                logger.info(f"🔄 Rescheduled job to {new_scheduled_time}")
            
            return bool(result)
            
        except Exception as e:
            logger.error(f"Failed to reschedule job: {e}")
            return False
    
    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a scheduled job (remove from sorted set)
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if cancelled successfully
        """
        try:
            # Find and remove job by job_id
            # Need to scan all jobs since we can't query by field
            all_jobs = await self.client.zrange(self.sorted_set_key, 0, -1)
            
            removed = False
            for job_json in all_jobs:
                try:
                    job_data = json.loads(job_json)
                    if job_data.get('job_id') == job_id:
                        result = await self.client.zrem(self.sorted_set_key, job_json)
                        if result:
                            logger.info(f"❌ Cancelled job: {job_id}")
                            removed = True
                        break
                except json.JSONDecodeError:
                    continue
            
            if not removed:
                logger.warning(f"⚠️ Job {job_id} not found for cancellation")
            
            return removed
            
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return False
    
    async def get_jobs_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all scheduled jobs for a specific user
        
        Args:
            user_id: User UUID
            
        Returns:
            List of job dictionaries
        """
        try:
            all_jobs = await self.client.zrange(self.sorted_set_key, 0, -1, withscores=True)
            
            user_jobs = []
            for job_json, score in all_jobs:
                try:
                    job_data = json.loads(job_json)
                    if job_data.get('user_id') == user_id:
                        job_data['score'] = score
                        user_jobs.append(job_data)
                except json.JSONDecodeError:
                    continue
            
            return user_jobs
            
        except Exception as e:
            logger.error(f"Failed to get jobs for user {user_id}: {e}")
            return []
    
    async def cancel_jobs_for_event(self, event_id: str) -> int:
        """
        Cancel all jobs related to a specific event
        Used when event is deleted or rescheduled
        
        Args:
            event_id: Event UUID
            
        Returns:
            Number of jobs cancelled
        """
        try:
            all_jobs = await self.client.zrange(self.sorted_set_key, 0, -1)
            
            cancelled_count = 0
            for job_json in all_jobs:
                try:
                    job_data = json.loads(job_json)
                    payload = job_data.get('payload', {})
                    if payload.get('event_id') == event_id:
                        result = await self.client.zrem(self.sorted_set_key, job_json)
                        if result:
                            cancelled_count += 1
                except json.JSONDecodeError:
                    continue
            
            if cancelled_count > 0:
                logger.info(f"❌ Cancelled {cancelled_count} job(s) for event {event_id}")
            
            return cancelled_count
            
        except Exception as e:
            logger.error(f"Failed to cancel jobs for event {event_id}: {e}")
            return 0
    
    async def get_scheduler_stats(self) -> Dict[str, Any]:
        """
        Get scheduler statistics
        
        Returns:
            Statistics dictionary
        """
        try:
            pending_count = await self.client.zcard(self.sorted_set_key)
            processing_count = await self.client.zcard(self.processing_set_key)
            
            # Get next job time
            next_jobs = await self.client.zrange(
                self.sorted_set_key,
                0,
                0,
                withscores=True
            )
            
            next_job_time = None
            if next_jobs:
                _, score = next_jobs[0]
                next_job_time = datetime.fromtimestamp(score).isoformat()
            
            return {
                "pending_jobs": pending_count,
                "processing_jobs": processing_count,
                "next_job_time": next_job_time,
                "current_time": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get scheduler stats: {e}")
            return {}
    
    async def cleanup_stuck_jobs(self, timeout_seconds: int = 300) -> int:
        """
        Clean up jobs stuck in processing state (e.g., after worker crash)
        
        Args:
            timeout_seconds: How long before considering job stuck
            
        Returns:
            Number of jobs cleaned up
        """
        try:
            cutoff_time = time.time() - timeout_seconds
            
            # Get jobs in processing set older than timeout
            stuck_jobs = await self.client.zrangebyscore(
                self.processing_set_key,
                min=0,
                max=cutoff_time
            )
            
            if not stuck_jobs:
                return 0
            
            # Move back to main set with current time + 60s (retry soon)
            retry_time = time.time() + 60
            
            cleaned = 0
            for job_json in stuck_jobs:
                pipeline = self.client.pipeline()
                pipeline.zrem(self.processing_set_key, job_json)
                pipeline.zadd(self.sorted_set_key, {job_json: retry_time})
                await pipeline.execute()
                cleaned += 1
            
            if cleaned > 0:
                logger.warning(f"🔧 Cleaned up {cleaned} stuck job(s)")
            
            return cleaned
            
        except Exception as e:
            logger.error(f"Failed to cleanup stuck jobs: {e}")
            return 0
