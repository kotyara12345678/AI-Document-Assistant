"""Background job API endpoints.

GET    /jobs          — list user's jobs
GET    /jobs/{id}     — get job detail
POST   /jobs/{id}/cancel — cancel a queued/running job
POST   /jobs          — submit a new background job
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.ratelimit import AGENT_BURST_LIMIT, AGENT_BURST_WINDOW, throttle
from app.core.security import get_current_user_id
from app.database.session import get_db
from app.schemas.job import (
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from app.services import job as job_service

logger = logging.getLogger("app.api.jobs")

router = APIRouter()


@router.get("", response_model=JobListResponse)
def list_jobs(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> JobListResponse:
    jobs = job_service.get_user_jobs(db, user_id, limit=100)
    return JobListResponse(
        jobs=[
            JobResponse(
                id=j.id,
                type=j.type,
                status=j.status,
                created_at=j.created_at,
                started_at=j.started_at,
                finished_at=j.finished_at,
                result=j.result,
                error=j.error,
                chat_id=j.chat_id,
            )
            for j in jobs
        ],
        total=len(jobs),
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> JobResponse:
    job = job_service.get_job(db, job_id, user_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return JobResponse(
        id=job.id,
        type=job.type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error,
        chat_id=job.chat_id,
    )


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> JobResponse:
    try:
        job = job_service.cancel_job(db, job_id, user_id)
    except job_service._JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    except job_service._JobAlreadyFinishedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {exc.status}",
        )
    return JobResponse(
        id=job.id,
        type=job.type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error,
        chat_id=job.chat_id,
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    request: JobCreateRequest,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> JobResponse:
    if not throttle.allow(f"agent:{user_id}", AGENT_BURST_LIMIT, AGENT_BURST_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please slow down",
        )
    try:
        job = job_service.create_job(
            db,
            user_id=user_id,
            chat_id=request.chat_id,
            job_type="agent",
            payload={
                "question": request.question,
                "chat_id": request.chat_id,
                "context_document_ids": request.context_document_ids,
                "document_id": request.document_id,
                "document_ids": request.document_ids,
            },
        )
    except job_service._JobLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )
    return JobResponse(
        id=job.id,
        type=job.type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error,
        chat_id=job.chat_id,
    )
