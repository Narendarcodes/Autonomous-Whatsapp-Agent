"""
Agent Engine
Orchestrates LLM
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from pydantic import ValidationError

from app.core.config import settings
from app.core.logging import logger
from app.models.user import User
from app.services.llm_factory import llm_service
from app.services.calendar_service import calendar_service
from app.tools.registry import tools_registry
from app.db.redis_client import redis_client
from app.schemas.calendar import CreateEventRequest, UpdateEventRequest
from app.schemas.tools import (
    GetEventsArguments,
    CreateEventArguments,
    UpdateEventArguments,
    DeleteEventArguments,
    SearchEventsArguments
)
from app.infrastructure.delayed_scheduler import DelayedJobScheduler
from app.services.proactive_scheduler import ProactiveScheduler


class AgentEngine:
    """Agent engine for processing user messages with LLM and tools"""
    
    def __init__(self):
        self.max_iterations = settings.AGENT_MAX_ITERATIONS
        self.system_prompt = self._build_system_prompt()
        # Initialize scheduler for reminder management
        delayed_scheduler = DelayedJobScheduler(redis_client)
        self.proactive_scheduler = ProactiveScheduler(delayed_scheduler)
    
    # Common valid timezones for quick validation
    VALID_TIMEZONES = {
        "UTC", "GMT", "America/New_York", "America/Los_Angeles", "America/Chicago",
        "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai",
        "Asia/Kolkata", "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland"
    }
    
    def _validate_timezone(self, timezone: str) -> str:
        """Validate timezone string and return safe value"""
        if not timezone or not isinstance(timezone, str):
            return "UTC"
        
        # Sanitize input
        timezone = timezone.strip()[:50]  # Limit length
        
        # Quick check for common timezones
        if timezone in self.VALID_TIMEZONES:
            return timezone
        
        # Try to validate with ZoneInfo
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone)  # Will raise if invalid
            return timezone
        except Exception:
            logger.warning(f"Invalid timezone '{timezone}', defaulting to UTC")
            return "UTC"
    
    def _sanitize_error_message(self, error: Exception) -> str:
        """Sanitize error message to prevent information leakage"""
        error_str = str(error)
        
        # Remove sensitive information patterns
        import re
        # Remove file paths
        error_str = re.sub(r'[A-Za-z]:\\[^\s]+', '[path]', error_str)
        error_str = re.sub(r'/[^\s]+\.py', '[file]', error_str)
        # Remove line numbers
        error_str = re.sub(r'line \d+', 'line [N]', error_str)
        # Remove IP addresses
        error_str = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[IP]', error_str)
        # Remove tokens/keys (long alphanumeric strings)
        error_str = re.sub(r'[a-zA-Z0-9]{32,}', '[token]', error_str)
        
        # Truncate to reasonable length
        if len(error_str) > 200:
            error_str = error_str[:200] + "..."
        
        return error_str
    
    def _build_system_prompt(self, user_timezone: str = "UTC") -> str:
        """Build system prompt for the agent"""
        return f"""You are a warm, friendly personal assistant and mentor who helps manage Google Calendar via WhatsApp. Think of yourself as a supportive friend who genuinely cares about the user's well-being and productivity.

🌟 YOUR PERSONALITY:
- Be warm, encouraging, and conversational - like a caring friend
- Use a friendly tone with occasional emojis to feel more personal
- Show genuine interest in what the user is working on
- Never be pushy or demanding about tasks - be supportive instead
- Celebrate small wins and acknowledge their efforts
- If they seem busy or stressed, offer words of encouragement

💬 HOW TO INTERACT:
- Ask gentle questions like "How's your day going?" or "Working on anything exciting?"
- When they have many events, say something like "Looks like a busy day ahead! Remember to take breaks 😊"
- If they're free, suggest: "Looks like you have some free time - perfect for that hobby or just relaxing!"
- Use phrases like "No pressure, but...", "Whenever you're ready...", "Just a friendly reminder..."
- Acknowledge their feelings: "That sounds like a lot! You've got this 💪"

📅 YOUR CAPABILITIES:
- Get upcoming events and schedules
- Create new calendar events (one-time OR recurring)
- Create RECURRING events for daily, weekly, monthly reminders - use the recurrence parameter!
- Update existing events  
- Delete/cancel events
- Search for specific events
- Offer friendly suggestions about time management

🔁 RECURRING EVENTS:
- When user says "daily", "every day", "weekly", "every week", "monthly" - create a RECURRING event
- Use the recurrence parameter with frequency set to DAILY, WEEKLY, or MONTHLY
- Example: "Remind me daily at 6pm" → create event with recurrence containing frequency=DAILY
- You can also set: interval (every N days), count (number of times), until (end date), by_day (specific weekdays)

⚠️ CRITICAL RULES:
1. ALWAYS use tools to check the actual calendar - NEVER make up or guess event information
2. If user asks about their events/schedule, you MUST call get_upcoming_events tool first
3. NEVER invent fake events - only report actual calendar data
4. Keep responses concise but warm (3-5 sentences)
5. If calendar is empty, be positive: "Your schedule is clear! A great opportunity to focus on what matters to you 🌟"
6. Only send ONE response message - combine all information into a single reply

🕐 TIMEZONE & TIME HANDLING:
- User's timezone: {user_timezone}
- When user says "10:30" without AM/PM, gently ask: "Is that 10:30 in the morning or evening? 😊"
- ALWAYS confirm the time in user's timezone after creating events
- Current date and time in user's timezone: {{current_time}}

🔄 CORRECTION HANDLING:
- If user corrects a previous request, handle it gracefully:
  1. Delete the incorrectly created event
  2. Create the new event with correct time
  3. Confirm cheerfully: "All fixed! I've updated it to [correct time] ✨"
- Look for correction keywords: "not", "no", "wrong", "actually", "I meant"

🔍 DUPLICATE DETECTION:
- Before creating an event, check if a similar event already exists
- If found, ask kindly: "I noticed you already have '[Event Name]' at [time]. Would you like me to update it or add this as a separate event?"

💡 RESPONSE EXAMPLES:
- Greeting: "Hey there! 👋 How can I help you today?"
- Empty schedule: "Your schedule is looking clear! ✨ Great time to focus on yourself or tackle that project you've been thinking about."
- Busy day: "Looks like you have a packed day! 📅 Remember to grab some water and take a breather between meetings 💪"
- Event created: "All set! ✅ I've added '[Event]' for [time]. You're all organized!"
- Checking in: "How did that meeting go? Anything else I can help you with?"

🎯 MENTORSHIP APPROACH:
- Don't push them to complete tasks - instead, ask how you can support them
- If they mention stress, offer encouragement: "It's okay to take things one step at a time"
- Suggest breaks: "You've been productive today! Maybe time for a quick stretch?"
- Be their cheerleader, not their taskmaster

Remember: You're a friendly guide, not a demanding manager. Make them feel supported and capable! 🌈"""
    
    async def process_message(
        self,
        user: User,
        message: str,
        db: Session
    ) -> str:
        """
        Process user message and generate response
        
        Args:
            user: User object
            message: User message text
            db: Database session
            
        Returns:
            Response text to send back
        """
        try:
            logger.info(f"🤖 Processing message for user {user.wa_phone}")
            logger.debug(f"Message: {message[:100]}...")
            
            # Load conversation history
            conversation = await redis_client.get_conversation(user.wa_phone)
            
            # Build messages list with user timezone
            user_timezone = getattr(user, 'timezone', 'UTC')
            messages = self._build_messages(message, conversation, user_timezone)
            
            # Get available tools
            tools = tools_registry.get_all_tools()
            
            # Start agent loop
            response_text = await self._agent_loop(
                user=user,
                messages=messages,
                tools=tools,
                db=db
            )
            
            # Save assistant response to conversation
            await redis_client.add_message(
                user_id=user.wa_phone,
                role="assistant",
                content=response_text
            )
            
            logger.info(f"✅ Agent processing complete")
            
            return response_text
            
        except Exception as e:
            logger.error(f"Agent processing error: {e}", exc_info=True)
            # Don't expose internal errors to users
            return "I apologize, but I encountered an error processing your request. Please try again in a moment."
    
    def _build_messages(
        self,
        current_message: str,
        conversation: List[Dict[str, Any]],
        user_timezone: str = "UTC"
    ) -> List[Dict[str, str]]:
        """
        Build messages list for LLM
        
        Args:
            current_message: Current user message
            conversation: Previous conversation history
            user_timezone: User's timezone
            
        Returns:
            List of message dictionaries
        """
        messages = []
        
        # Add system prompt with timezone
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        # Validate and sanitize timezone
        safe_timezone = self._validate_timezone(user_timezone)
        
        try:
            tz = ZoneInfo(safe_timezone)
            current_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        system_prompt = self._build_system_prompt(user_timezone).format(
            current_time=current_time
        )
        messages.append({
            "role": "system",
            "content": system_prompt
        })
        
        # Add conversation history (reverse order - oldest first)
        if conversation:
            for msg in reversed(conversation):
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Add current message
        messages.append({
            "role": "user",
            "content": current_message
        })
        
        return messages
    
    async def _agent_loop(
        self,
        user: User,
        messages: List[Dict[str, str]],
        tools: List,
        db: Session
    ) -> str:
        """
        Main agent loop with tool calling
        
        Args:
            user: User object
            messages: Messages list
            tools: Available tools
            db: Database session
            
        Returns:
            Final response text
        """
        iteration = 0
        has_executed_tools = False
        
        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"Agent iteration {iteration}/{self.max_iterations}")
            
            # Send to LLM (uses configured service from factory)
            response = await llm_service.chat_completion(
                messages=messages,
                tools=tools
            )
            
            message = response.get("message", {})
            
            # Check for tool calls
            tool_calls = llm_service.parse_tool_calls(message)
            
            if not tool_calls:
                # No tool calls - return response
                content = message.get("content", "")
                
                # If we executed tools, this is the final response
                if has_executed_tools:
                    logger.info(f"✅ Agent completed with final response after tool execution")
                    return content
                else:
                    # No tools executed - LLM responded directly without using tools
                    logger.warning(f"⚠️ Agent responded directly without calling any tools")
                    return content
            
            # Execute tool calls
            logger.info(f"🔧 Executing {len(tool_calls)} tool calls")
            has_executed_tools = True
            
            # Add assistant message with tool calls
            messages.append(message)
            
            # Execute each tool
            for tool_call in tool_calls:
                tool_name = tool_call.function.get("name")
                tool_args_str = tool_call.function.get("arguments", "{}")
                
                try:
                    tool_args = json.loads(tool_args_str)
                except json.JSONDecodeError:
                    tool_args = {}
                
                logger.info(f"🔧 Executing: {tool_name}")
                logger.debug(f"Arguments: {tool_args}")
                
                # Execute tool
                tool_result = await self._execute_tool(
                    user=user,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    db=db
                )
                
                # Format result for LLM
                result_message = llm_service.format_tool_result(
                    tool_call_id=tool_call.id,
                    tool_name=tool_name,
                    result=tool_result
                )
                
                messages.append(result_message)
            
            # After executing tools, loop back to LLM to get final response
            # DO NOT return here - let LLM formulate the response to user
        
        # Max iterations reached
        logger.warning(f"Max iterations ({self.max_iterations}) reached")
        return "I've completed your request, but the process took longer than expected. Please let me know if you need anything else."
    
    async def _execute_tool(
        self,
        user: User,
        tool_name: str,
        tool_args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """
        Execute a tool and return result
        
        Args:
            user: User object
            tool_name: Name of tool to execute
            tool_args: Tool arguments
            db: Database session
            
        Returns:
            Tool execution result
        """
        try:
            # Validate and sanitize arguments using Pydantic schemas
            validated_args = self._validate_tool_args(tool_name, tool_args)
            if "error" in validated_args:
                return validated_args
            
            if tool_name == "get_upcoming_events":
                return await self._tool_get_events(user, validated_args, db)
            

            elif tool_name == "create_calendar_event":
                # Check for recurrence fields
                recurrence = validated_args.get("recurrence")
                if recurrence:
                    # Use new recurring event service
                    return await self._tool_create_recurring_event(user, validated_args, db)
                else:
                    return await self._tool_create_event(user, validated_args, db)

            
            elif tool_name == "update_calendar_event":
                return await self._tool_update_event(user, validated_args, db)
            
            elif tool_name == "delete_calendar_event":
                return await self._tool_delete_event(user, validated_args, db)
            
            elif tool_name == "search_calendar_events":
                return await self._tool_search_events(user, validated_args, db)
            
            else:
                logger.error(f"Unknown tool: {tool_name}")
                return {"error": f"Unknown tool: {tool_name}"}
                
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return {"error": str(e)}
    
    def _validate_tool_args(
        self,
        tool_name: str,
        tool_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate tool arguments using Pydantic schemas
        
        Args:
            tool_name: Name of the tool
            tool_args: Raw arguments from LLM
            
        Returns:
            Validated/sanitized arguments dict or error dict
        """
        # Map tool names to their validation schemas
        tool_validators = {
            "get_upcoming_events": GetEventsArguments,
            "create_calendar_event": CreateEventArguments,
            "update_calendar_event": UpdateEventArguments,
            "delete_calendar_event": DeleteEventArguments,
            "search_calendar_events": SearchEventsArguments,
        }
        
        validator_class = tool_validators.get(tool_name)
        
        if not validator_class:
            # No validator defined - pass through with basic sanitization
            logger.warning(f"No validator for tool: {tool_name}, using raw args")
            return self._sanitize_args(tool_args)
        
        try:
            # Validate using Pydantic model
            validated = validator_class(**tool_args)
            # Return as dict, excluding None values to keep args clean
            return validated.model_dump(exclude_none=True)
        except ValidationError as e:
            error_messages = []
            for error in e.errors():
                field = ".".join(str(loc) for loc in error["loc"])
                msg = error["msg"]
                error_messages.append(f"{field}: {msg}")
            
            error_str = "; ".join(error_messages)
            logger.error(f"Tool argument validation failed for {tool_name}: {error_str}")
            return {"error": f"Invalid arguments: {error_str}"}
    
    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Basic sanitization for unvalidated arguments
        
        Args:
            args: Raw arguments dict
            
        Returns:
            Sanitized arguments dict
        """
        sanitized = {}
        for key, value in args.items():
            # Strip strings to prevent whitespace injection
            if isinstance(value, str):
                sanitized[key] = value.strip()[:10000]  # Limit string length
            elif isinstance(value, list):
                # Sanitize list items
                sanitized[key] = [
                    item.strip()[:1000] if isinstance(item, str) else item
                    for item in value[:100]  # Limit list length
                ]
            elif isinstance(value, (int, float, bool, type(None))):
                sanitized[key] = value
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_args(value)  # Recursive
            # Skip unknown types for safety
        return sanitized
    
    async def _tool_get_events(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute get_upcoming_events tool"""
        try:
            # Parse arguments
            time_range = args.get("time_range")
            start_date = args.get("start_date")
            end_date = args.get("end_date")
            max_results = args.get("max_results", 10)
            
            # Determine date range
            if time_range:
                start_dt, end_dt = tools_registry.parse_time_range(time_range)
            elif start_date and end_date:
                start_dt = datetime.fromisoformat(start_date)
                end_dt = datetime.fromisoformat(end_date)
            else:
                # Default: today
                start_dt, end_dt = tools_registry.parse_time_range("today")
            
            # Fetch events
            events = await calendar_service.list_events(
                user=user,
                db=db,
                start_date=start_dt,
                end_date=end_dt,
                max_results=max_results
            )
            
            if not events:
                return {
                    "success": True,
                    "events": [],
                    "count": 0,
                    "message": "No events found in your calendar for this time period."
                }
            
            # Format events concisely
            formatted_events = []
            for event in events:
                summary = event.get("summary", "Untitled")
                
                # Parse time
                start = event.get('start', {})
                if 'dateTime' in start:
                    start_dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
                    time_str = start_dt.strftime('%b %d at %I:%M %p')
                elif 'date' in start:
                    start_dt = datetime.fromisoformat(start['date'])
                    time_str = start_dt.strftime('%b %d (All day)')
                else:
                    time_str = "Unknown time"
                
                location = event.get('location', '')
                
                formatted_events.append({
                    "id": event.get("id"),
                    "summary": summary,
                    "time": time_str,
                    "location": location
                })
            
            return {
                "success": True,
                "count": len(events),
                "events": formatted_events,
                "message": f"Found {len(events)} event(s)"
            }
            
        except Exception as e:
            logger.error(f"get_events tool error: {e}")
            error_str = str(e).lower()
            
            # Check if it's an OAuth error
            if 'invalid_grant' in error_str or 'token' in error_str and ('expired' in error_str or 'revoked' in error_str):
                oauth_url = f"{settings.BASE_URL}/oauth/login?phone={user.wa_phone}"
                return {
                    "success": False, 
                    "error": "OAUTH_EXPIRED",
                    "message": f"Your Google Calendar connection has expired. Please reconnect by visiting: {oauth_url}"
                }
            
            return {"success": False, "error": str(e)}
    
    async def _tool_create_event(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute create_calendar_event tool"""
        try:
            # Parse arguments
            event_data = CreateEventRequest(
                summary=args["summary"],
                start_time=datetime.fromisoformat(args["start_time"]),
                end_time=datetime.fromisoformat(args["end_time"]),
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees")
            )
            
            # Create event
            event = await calendar_service.create_event(
                user=user,
                db=db,
                event_data=event_data
            )
            
            # Schedule reminders for new event
            try:
                from sqlalchemy import select
                from app.models.user import EventCache
                query = select(EventCache).where(EventCache.event_id == event.get("id"))
                result = await db.execute(query)
                event_cache = result.scalar_one_or_none()
                
                if event_cache:
                    await self.proactive_scheduler.schedule_event_reminders(
                        db=db,
                        user_id=str(user.id),
                        event=event_cache
                    )
                    logger.info(f"✅ Scheduled reminders for new event {event.get('id')}")
            except Exception as e:
                logger.error(f"Failed to schedule reminders for new event: {e}")
            
            return {
                "success": True,
                "event_id": event.get("id"),
                "summary": event.get("summary"),
                "message": f"Event {event.get('summary')} created successfully!"
            }
            
        except Exception as e:
            logger.error(f"create_event tool error: {e}")
            return {"success": False, "error": str(e)}
    

    async def _tool_create_recurring_event(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute create_calendar_event tool for recurring events"""
        try:
            from app.services.calendar_service_recurring import CalendarServiceRecurring
            
            # Parse arguments into Pydantic model
            event_data = CreateEventRequest(
                summary=args["summary"],
                start_time=datetime.fromisoformat(args["start_time"]),
                end_time=datetime.fromisoformat(args["end_time"]),
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees")
            )
            recurrence_rule = args.get("recurrence")
            
            # Instantiate service
            service = CalendarServiceRecurring()
            
            # Create master event and instances
            # Note: create_recurring_event returns the master event dict or object
            result = await service.create_recurring_event(
                user=user,
                db=db,
                event_data=event_data,
                recurrence_rule=recurrence_rule
            )
            
            # The result from create_recurring_event is {"id": ..., "summary": ...}
            # We need to find the instances to schedule reminders.
            # The service logs "Created recurring event ... with X instances" but doesn't return instance IDs directly in the dict.
            # We can query them.
            
            master_event_id = result.get("id")
            
            # Find all instances for this master event
            from sqlalchemy import select
            from app.models.user import EventCache
            
            # Get instances (including master if it's in cache, but master has is_recurring=True)
            # We want to schedule reminders for ALL instances (expanded ones)
            # Expanded instances have recurring_event_id = master_event_id
            
            query = select(EventCache).where(EventCache.recurring_event_id == master_event_id)
            res = await db.execute(query)
            instances = res.scalars().all()
            
            scheduled = 0
            for instance in instances:
                try:
                    await self.proactive_scheduler.schedule_event_reminders(
                        db=db,
                        user_id=str(user.id),
                        event=instance
                    )
                    scheduled += 1
                except Exception as e:
                    logger.error(f"Failed to schedule reminder for instance {instance.event_id}: {e}")
            
            return {
                "success": True,
                "event_id": master_event_id,
                "instance_count": len(instances),
                "scheduled_reminders": scheduled,
                "message": f"Recurring event created with {len(instances)} instances and reminders scheduled."
            }
            
        except Exception as e:
            logger.error(f"create_recurring_event tool error: {e}")
            return {"success": False, "error": str(e)}

    async def _tool_update_event(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute update_calendar_event tool"""
        try:
            # Parse arguments
            update_data = UpdateEventRequest(
                event_id=args["event_id"],
                summary=args.get("summary"),
                start_time=datetime.fromisoformat(args["start_time"]) if args.get("start_time") else None,
                end_time=datetime.fromisoformat(args["end_time"]) if args.get("end_time") else None,
                description=args.get("description"),
                location=args.get("location"),
                attendees=args.get("attendees"),
                status=args.get("status")
            )
            
            # Update event
            event = await calendar_service.update_event(
                user=user,
                db=db,
                event_data=update_data
            )
            
            # Cancel old reminders and schedule new ones (if time changed)
            if args.get("start_time"):
                try:
                    # Cancel old reminders
                    await self.proactive_scheduler.cancel_event_reminders(
                        db=db,
                        event_id=args["event_id"]
                    )
                    
                    # Get updated event from cache
                    from sqlalchemy import select
                    from app.models.user import EventCache
                    query = select(EventCache).where(EventCache.event_id == args["event_id"])
                    result = await db.execute(query)
                    updated_event_cache = result.scalar_one_or_none()
                    
                    # Schedule new reminders
                    if updated_event_cache:
                        await self.proactive_scheduler.schedule_event_reminders(
                            db=db,
                            user_id=str(user.id),
                            event=updated_event_cache
                        )
                        logger.info(f"✅ Rescheduled reminders for updated event {args['event_id']}")
                except Exception as e:
                    logger.error(f"Failed to reschedule reminders: {e}")
            
            return {
                "success": True,
                "event_id": event.get("id"),
                "message": f"Event updated successfully!"
            }
            
        except Exception as e:
            logger.error(f"update_event tool error: {e}")
            return {"success": False, "error": str(e)}
    
    async def _tool_delete_event(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute delete_calendar_event tool"""
        try:
            event_id = args["event_id"]
            
            # Delete event
            success = await calendar_service.delete_event(
                user=user,
                db=db,
                event_id=event_id
            )
            
            if success:
                # Cancel all reminders for deleted event
                try:
                    cancelled_count = await self.proactive_scheduler.cancel_event_reminders(
                        db=db,
                        event_id=event_id
                    )
                    logger.info(f"✅ Cancelled {cancelled_count} reminders for deleted event {event_id}")
                except Exception as e:
                    logger.error(f"Failed to cancel reminders: {e}")
                
                return {
                    "success": True,
                    "message": "Event deleted successfully!"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to delete event"
                }
            
        except Exception as e:
            logger.error(f"delete_event tool error: {e}")
            return {"success": False, "error": str(e)}
    
    async def _tool_search_events(
        self,
        user: User,
        args: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Execute search_calendar_events tool"""
        try:
            query = args["query"]
            max_results = args.get("max_results", 10)
            
            # Search events
            events = await calendar_service.search_events(
                user=user,
                db=db,
                query=query,
                max_results=max_results
            )
            
            if not events:
                return {
                    "success": True,
                    "events": [],
                    "message": f"No events found matching {query}"
                }
            
            # Format events
            formatted_events = []
            for event in events:
                formatted = tools_registry.format_event_for_display(event)
                formatted_events.append({
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "formatted": formatted
                })
            
            return {
                "success": True,
                "count": len(events),
                "events": formatted_events
            }
            
        except Exception as e:
            logger.error(f"search_events tool error: {e}")
            return {"success": False, "error": str(e)}


# Global instance
agent_engine = AgentEngine()
