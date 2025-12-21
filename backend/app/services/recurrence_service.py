"""
Recurrence Rule Parser
Handles expansion of recurring events using RRULE format
"""

from typing import List, Optional
from datetime import datetime, timedelta
from dateutil.rrule import rrulestr
from app.core.logging import logger

class RecurrenceService:
    """Service for parsing and expanding recurring events"""
    
    @staticmethod
    def expand_recurrence(
        start_time: datetime,
        recurrence_rule: str,
        horizon_days: int = 60,
        max_instances: int = 100
    ) -> List[datetime]:
        """
        Expand recurrence rule into list of datetime instances
        """
        try:
            rrule = rrulestr(recurrence_rule, dtstart=start_time)
            end_date = datetime.utcnow() + timedelta(days=horizon_days)
            instances = []
            for instance in rrule:
                if instance > end_date:
                    break
                if len(instances) >= max_instances:
                    logger.warning(f"Hit max_instances limit ({max_instances}) for recurrence expansion")
                    break
                instances.append(instance)
            logger.info(f"Expanded recurrence into {len(instances)} instances (next {horizon_days} days)")
            return instances
        except Exception as e:
            logger.error(f"Failed to expand recurrence rule: {e}")
            return [start_time]

    @staticmethod
    def parse_natural_language_recurrence(text: str) -> Optional[str]:
        text = text.lower().strip()
        if text in ["daily", "every day", "everyday"]:
            return "RRULE:FREQ=DAILY"
        weekday_map = {
            "monday": "MO", "mon": "MO",
            "tuesday": "TU", "tue": "TU",
            "wednesday": "WE", "wed": "WE",
            "thursday": "TH", "thu": "TH",
            "friday": "FR", "fri": "FR",
            "saturday": "SA", "sat": "SA",
            "sunday": "SU", "sun": "SU"
        }
        for day_name, day_code in weekday_map.items():
            if f"every {day_name}" in text:
                return f"RRULE:FREQ=WEEKLY;BYDAY={day_code}"
        if "every weekday" in text or "weekdays" in text:
            return "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        if "every weekend" in text:
            return "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"
        if "every week" in text or "weekly" in text:
            return "RRULE:FREQ=WEEKLY"
        if "every 2 weeks" in text or "biweekly" in text:
            return "RRULE:FREQ=WEEKLY;INTERVAL=2"
        if "monthly" in text or "every month" in text:
            return "RRULE:FREQ=MONTHLY"
        if "first" in text and "month" in text:
            for day_name, day_code in weekday_map.items():
                if day_name in text:
                    return f"RRULE:FREQ=MONTHLY;BYDAY=1{day_code}"
        if "last" in text and "month" in text:
            for day_name, day_code in weekday_map.items():
                if day_name in text:
                    return f"RRULE:FREQ=MONTHLY;BYDAY=-1{day_code}"
        return None

    @staticmethod
    def validate_rrule(recurrence_rule: str) -> bool:
        try:
            rrulestr(recurrence_rule)
            return True
        except:
            return False
