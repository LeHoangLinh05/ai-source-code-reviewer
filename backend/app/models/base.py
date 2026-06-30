"""Shared SQLAlchemy declarative base for all relational models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class used by SQLAlchemy models and future Alembic migrations."""
