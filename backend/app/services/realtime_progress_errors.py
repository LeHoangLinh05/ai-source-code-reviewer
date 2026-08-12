"""Error classification for best-effort realtime progress publishing."""

from redis.exceptions import RedisError

EVENT_LOOP_CLOSED_MESSAGE = "event loop is closed"


def is_realtime_progress_error(error: RedisError | RuntimeError) -> bool:
    """Return whether a realtime progress failure should not fail the job."""

    if isinstance(error, RedisError):
        return True

    return EVENT_LOOP_CLOSED_MESSAGE in str(error).lower()
