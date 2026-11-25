"""Promise and Job inspection/cancellation endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/promises")
async def list_promises(
    request: Request,
    status: Optional[str] = None,
    limit: int = 100,
):
    limit = max(1, min(limit, 500))
    return {
        "promises": request.app.state.jobs.promises(
            status=status,
            limit=limit,
        )
    }


@router.get("/promises/{promise_id}")
async def get_promise(promise_id: str, request: Request):
    promise = request.app.state.jobs.promise(promise_id)
    if promise is None:
        raise HTTPException(status_code=404, detail=f"promise not found: {promise_id}")
    return promise


@router.post("/promises/{promise_id}/cancel")
async def cancel_promise(promise_id: str, request: Request):
    try:
        status = request.app.state.jobs.cancel_promise(promise_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {"promise_id": promise_id, "status": status}


@router.get("/jobs")
async def list_jobs(
    request: Request,
    status: Optional[str] = None,
    limit: int = 100,
):
    limit = max(1, min(limit, 500))
    return {
        "jobs": request.app.state.jobs.jobs(
            status=status,
            limit=limit,
        )
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = request.app.state.jobs.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    try:
        status = request.app.state.jobs.cancel_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return {"job_id": job_id, "status": status}
