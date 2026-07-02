"""Background review worker task skeleton."""

from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.review_worker.ping")
def ping() -> str:
    """Return a simple response for worker connectivity checks."""

    return "pong"
