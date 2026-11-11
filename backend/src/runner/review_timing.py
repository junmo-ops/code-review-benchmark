"""Review wall time from service timestamps, never from local query attempts."""
from datetime import datetime
import math


def service_duration(timing):
    if not isinstance(timing, dict):
        return None
    start, end = timing.get('trigger_at'), timing.get('completed_at')
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        seconds = (datetime.fromisoformat(end.replace('Z', '+00:00'))
                   - datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None
    return round(seconds, 3) if seconds >= 0 and math.isfinite(seconds) else None


def with_review_duration(value):
    """Return a presentation copy; old scored answers must remain byte-identical."""
    if not isinstance(value, dict) or value.get('reviewer') != 'server_agent':
        return value
    service = value.get('service')
    if not isinstance(service, dict):
        return value
    seconds = service_duration(service.get('timing'))
    if seconds is None:
        return value
    return dict(value, duration_seconds=seconds, duration_source='service_trigger_to_completion')
