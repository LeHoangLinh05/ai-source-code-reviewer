"""Celery CLI entrypoint for the RepoGuard AI worker."""

from app.workers.celery_app import celery_app

app = celery_app
celery = celery_app

__all__ = ["app", "celery", "celery_app"]
