"""Celery application configuration for background jobs."""

import ssl
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import get_settings

settings = get_settings()


def build_celery_redis_url(redis_url: str) -> str:
    """Add required SSL parameters for Celery when Redis uses TLS."""

    if not redis_url.startswith("rediss://"):
        return redis_url

    parsed_url = urlsplit(redis_url)
    query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    query_params.setdefault("ssl_cert_reqs", "CERT_REQUIRED")

    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query_params),
            parsed_url.fragment,
        )
    )


celery_broker_url = build_celery_redis_url(settings.celery_broker_url)
celery_result_backend = build_celery_redis_url(settings.celery_result_backend)
redis_ssl_options = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

celery_app = Celery(
    "repoguard_ai",
    broker=celery_broker_url,
    backend=celery_result_backend,
    include=[
        "app.workers.fix_publish_worker",
        "app.workers.fix_worker",
        "app.workers.review_worker",
        "app.workers.sandbox_cleanup_worker",
    ],
)

celery_app.conf.update(
    broker_url=celery_broker_url,
    result_backend=celery_result_backend,
    broker_use_ssl=redis_ssl_options
    if celery_broker_url.startswith("rediss://")
    else None,
    redis_backend_use_ssl=redis_ssl_options
    if celery_result_backend.startswith("rediss://")
    else None,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "cleanup-expired-sandboxes": {
            "task": "app.workers.sandbox_cleanup_worker.cleanup_expired_sandboxes",
            "schedule": 30 * 60,
        },
    },
)
