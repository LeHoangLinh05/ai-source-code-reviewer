"""Mapping helpers for fix job API responses."""

from app.models.fix_job import FixJob, FixPublishStatus
from app.schemas.fix_job import FixJobResponse, normalize_fix_validation_result

FIX_STREAM_PATH_PREFIX = "/api/fixes"


def build_fix_job_response(fix_job: FixJob) -> FixJobResponse:
    """Return a stable API response for a fix job ORM entity."""

    validation_summary = normalize_fix_validation_result(
        validation_status=fix_job.validation_status,
        validation_output=fix_job.validation_output,
    )
    publish_status = fix_job.publish_status or FixPublishStatus.NOT_REQUESTED
    return FixJobResponse(
        id=fix_job.id,
        review_job_id=fix_job.review_job_id,
        user_id=fix_job.user_id,
        status=fix_job.status,
        validation_status=fix_job.validation_status,
        issue_ids=fix_job.issue_ids,
        target_branch=fix_job.target_branch,
        base_commit_sha=fix_job.base_commit_sha,
        fix_branch=fix_job.fix_branch,
        error_message=fix_job.error_message,
        failure_reason=fix_job.error_message,
        changed_files=fix_job.changed_files,
        validation_output=fix_job.validation_output,
        validation_summary=validation_summary,
        publish_status=publish_status,
        publish_error=fix_job.publish_error,
        published_branch=fix_job.published_branch,
        published_commit_sha=fix_job.published_commit_sha,
        provider=fix_job.provider,
        publish_started_at=fix_job.publish_started_at,
        publish_completed_at=fix_job.publish_completed_at,
        fork_repository_full_name=fix_job.fork_repository_full_name,
        fork_branch=fix_job.fork_branch,
        upstream_repository_full_name=fix_job.upstream_repository_full_name,
        pr_url=fix_job.pr_url,
        stream_url=build_fix_stream_url(fix_job.id),
        started_at=fix_job.started_at,
        completed_at=fix_job.completed_at,
        created_at=fix_job.created_at,
    )


def build_fix_stream_url(fix_job_id: object) -> str:
    """Return the owner-scoped SSE stream URL for a fix job."""

    return f"{FIX_STREAM_PATH_PREFIX}/{fix_job_id}/stream"
