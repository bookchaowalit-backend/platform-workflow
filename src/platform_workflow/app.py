"""Independent workflow HTTP app; authority always comes from shared JWT verification."""
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, Query
from fastapi.responses import JSONResponse
from platform_backend_core import create_app, require_scope
from pydantic import BaseModel, ConfigDict, Field

from .store import Conflict, InvalidInput, JobStore, NotFound, PermissionDenied


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EnqueueBody(StrictBody):
    queue: str = 'default'
    job_type: str
    payload: dict[str, Any]
    run_at: datetime | None = None
    max_attempts: int = Field(default=3, ge=1, le=100)


class ClaimBody(StrictBody):
    owner: str
    queues: list[str] = Field(default_factory=lambda:['default'], min_length=1, max_length=20)
    lease_seconds: int = Field(default=30, ge=1, le=900)


class LeaseBody(StrictBody):
    lease_token: UUID


class RenewBody(LeaseBody):
    lease_seconds: int = Field(default=30, ge=1, le=900)


class CompleteBody(LeaseBody):
    result: Any = None


class FailBody(LeaseBody):
    error_code: str


def public_job(row):
    return {k:v for k,v in row.items() if k not in {'lease_token','lease_owner','fingerprint','idempotency_key','operation'}}


def create_workflow_app(database, verifier):
    app = create_app('platform-workflow', database, verifier, max_body_bytes=100000)
    store = JobStore(database)
    app.state.job_store = store
    # Replace the shared connectivity probe with this service's schema gate.
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, 'path', None) != '/readyz']

    @app.get('/readyz', include_in_schema=False)
    def readyz():
        try:
            if not database.ready():
                raise RuntimeError('database unavailable')
            with database.transaction(UUID(int=0)) as conn:
                rows = conn.execute("""SELECT relrowsecurity,relforcerowsecurity,
                    has_schema_privilege(relnamespace,'USAGE'),
                    has_table_privilege(oid,'SELECT'),
                    has_table_privilege(oid,'INSERT'),
                    has_table_privilege(oid,'UPDATE')
                    FROM pg_catalog.pg_class
                    WHERE oid IN (to_regclass('workflow_jobs'),to_regclass('workflow_attempts'))""").fetchall()
                ready = len(rows) == 2 and all(all(value is True for value in row) for row in rows)
                conn.execute('SELECT id FROM workflow_jobs LIMIT 0')
                conn.execute('SELECT job_id FROM workflow_attempts LIMIT 0')
        except Exception:
            ready = False
        return JSONResponse({'status':'ready' if ready else 'unavailable'},status_code=200 if ready else 503)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({'detail':'job not found'},status_code=404)

    @app.exception_handler(PermissionDenied)
    async def denied(request, exc):
        return JSONResponse({'detail':'scope denied'},status_code=403)

    # Only the domain InvalidInput is a client error; any other exception (an unexpected
    # ValueError included) is a server bug and becomes the shared core's redacted 500.
    @app.exception_handler(InvalidInput)
    async def invalid(request, exc):
        return JSONResponse({'detail':'invalid job request'},status_code=422)

    @app.post('/v1/jobs', status_code=201)
    def enqueue(body:EnqueueBody, p=Depends(require_scope('jobs:write')),
                key:str=Header(alias='Idempotency-Key',min_length=1,max_length=200)):
        return public_job(store.enqueue(p,key=key,**body.model_dump()))

    @app.get('/v1/jobs')
    def listing(limit:int=Query(default=50,ge=1,le=100), after:UUID|None=None,
                p=Depends(require_scope('jobs:read'))):
        return {'items':[public_job(row) for row in store.list(p,limit=limit,after=after)]}

    @app.get('/v1/jobs/{job_id}')
    def get(job_id:UUID,p=Depends(require_scope('jobs:read'))):
        return public_job(store.get(p,job_id))

    @app.get('/v1/jobs/{job_id}/attempts')
    def attempts(job_id:UUID,p=Depends(require_scope('jobs:read'))):
        return {'items':[{k:v for k,v in row.items() if k not in {'lease_token','owner'}}
                         for row in store.attempts(p,job_id)]}

    @app.post('/v1/jobs/{job_id}/cancel')
    def cancel(job_id:UUID,p=Depends(require_scope('jobs:write'))):
        return public_job(store.cancel(p,job_id))

    @app.post('/v1/worker/claim')
    def claim(body:ClaimBody,p=Depends(require_scope('jobs:work'))):
        return store.claim(p,**body.model_dump())

    @app.post('/v1/worker/jobs/{job_id}/renew')
    def renew(job_id:UUID,body:RenewBody,p=Depends(require_scope('jobs:work'))):
        return store.renew(p,job_id,**body.model_dump())

    @app.post('/v1/worker/jobs/{job_id}/complete')
    def complete(job_id:UUID,body:CompleteBody,p=Depends(require_scope('jobs:work'))):
        return public_job(store.complete(p,job_id,**body.model_dump()))

    @app.post('/v1/worker/jobs/{job_id}/fail')
    def fail(job_id:UUID,body:FailBody,p=Depends(require_scope('jobs:work'))):
        return public_job(store.fail(p,job_id,**body.model_dump()))

    return app
