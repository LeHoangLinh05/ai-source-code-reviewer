"""Persistence operations for fix audit log entries."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fix_audit_log import FixAuditAction, FixAuditLog


class FixAuditLogRepository:
    """Append and read audit events for fix workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> FixAuditLog:
        """Append one audit event to a fix job timeline."""

        audit_log = FixAuditLog(
            fix_job_id=fix_job_id,
            user_id=user_id,
            action=action,
            message=message,
            event_metadata=event_metadata,
        )
        self.session.add(audit_log)
        await self.session.commit()
        await self.session.refresh(audit_log)
        return audit_log

    async def stage_append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> FixAuditLog:
        """Stage one audit event in the caller's transaction."""

        audit_log = FixAuditLog(
            fix_job_id=fix_job_id,
            user_id=user_id,
            action=action,
            message=message,
            event_metadata=event_metadata,
        )
        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def list_for_fix_job(self, fix_job_id: UUID) -> list[FixAuditLog]:
        """Return audit events for one fix job, oldest first."""

        statement = (
            select(FixAuditLog)
            .where(FixAuditLog.fix_job_id == fix_job_id)
            .order_by(FixAuditLog.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def rollback(self) -> None:
        """Discard staged audit log changes."""

        await self.session.rollback()
