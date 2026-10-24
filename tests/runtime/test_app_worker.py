from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from platform_backend_core import TokenVerifier
from psycopg import sql
from test_jobs import database, enqueue, principal

from platform_workflow import JobStore
from platform_workflow.app import create_workflow_app
from platform_workflow.worker import Worker


def test_verified_api_scope_workspace_and_worker_flow(database):
    db, _ = database
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = TokenVerifier(issuer='https://test.invalid', audience='workflow', public_key=public)
    def auth(ws, scopes):
        now = datetime.now(UTC)
        token = jwt.encode({'sub':str(uuid4()), 'ws':str(ws),'scope':' '.join(scopes),
            'iss':'https://test.invalid','aud':'workflow','iat':now,'exp':now+timedelta(minutes=5),'jti':str(uuid4())}, private, algorithm='RS256')
        return {'Authorization':'Bearer '+token}
    a, b = uuid4(), uuid4()
    with TestClient(create_workflow_app(db, verifier)) as client:
        assert client.get('/v1/jobs').status_code == 401
        assert client.get('/v1/jobs',headers=auth(a,[])).status_code == 403
        headers = auth(a,['jobs:write','jobs:read','jobs:work']) | {'Idempotency-Key':'create'}
        body = {'queue':'default','job_type':'echo','payload':{'a':1}}
        response = client.post('/v1/jobs',headers=headers,json=body)
        assert response.status_code == 201, response.text
        job_id = response.json()['id']
        assert client.post('/v1/jobs',headers=headers,json=body).json()['id'] == job_id
        assert client.post('/v1/jobs',headers=headers,json=body | {'payload':{}}).status_code == 409
        assert client.get('/v1/jobs/'+job_id,headers=auth(b,['jobs:read'])).status_code == 404
        assert client.get('/v1/jobs',headers=headers).json()['items'][0]['id'] == job_id
        claimed = client.post('/v1/worker/claim',headers=headers,json={'owner':'test'}).json()
        result = client.post('/v1/worker/jobs/'+job_id+'/complete',headers=headers,json={'lease_token':claimed['lease_token'],'result':{'ok':True}})
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'succeeded'
        assert 'lease_token' not in client.get('/v1/jobs/'+job_id,headers=headers).json()
        assert client.post('/v1/jobs',headers=headers,json=body | {'workspace_id':str(b)}).status_code == 422


def test_worker_injected_handlers_success_and_safe_failure(database):
    db, _ = database
    s, p = JobStore(db), principal()
    row = enqueue(s,p)
    worker = Worker(s,p,{'echo':lambda payload: {'echo':payload['x']}},owner='worker')
    assert worker.run_once()
    assert s.get(p,row['id'])['result'] == {'echo':1}
    assert worker.run_once() is False
    row = enqueue(s,p,max_attempts=1)
    def failing(payload):
        raise RuntimeError('private error must never be stored')
    worker = Worker(s,p,{'echo':failing},owner='worker')
    assert worker.run_once()
    job = s.get(p,row['id'])
    assert job['status'] == 'dead'
    assert job['error_code'] == 'handler_failed'


def test_readiness_denies_missing_service_schema(database):
    db, _ = database
    class UnusedVerifier:
        def verify(self, authorization):
            raise AssertionError('health must not request credentials')
    with TestClient(create_workflow_app(db, UnusedVerifier())) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/readyz').status_code == 200
        with db.transaction(uuid4()) as conn:
            conn.execute('DROP TABLE workflow_attempts')
            conn.execute('DROP TABLE workflow_jobs')
        assert client.get('/readyz').status_code == 503


def test_worker_renews_long_running_handler(database):
    import time
    db, _ = database
    s,p = JobStore(db),principal()
    row = enqueue(s,p)
    def handler(payload):
        time.sleep(1.4)
        return {'ok':True}
    assert Worker(s,p,{'echo':handler},owner='slow',lease_seconds=1).run_once()
    assert s.get(p,row['id'])['status'] == 'succeeded'


@pytest.mark.parametrize('unsupported', ['\x00', '\ud800', '\udfff'])
@pytest.mark.parametrize('in_key', [False, True])
def test_unsupported_json_strings_return_api_422(database, unsupported, in_key):
    import json
    db,_ = database
    p = principal()
    class FixedVerifier:
        def verify(self, authorization):
            return p
    payload = {'nested':[{unsupported:'value'} if in_key else {'value':unsupported}]}
    body = json.dumps({'job_type':'echo','payload':payload},ensure_ascii=True)
    with TestClient(create_workflow_app(db,FixedVerifier()),raise_server_exceptions=False) as client:
        response = client.post('/v1/jobs',headers={'Idempotency-Key':'invalid','Content-Type':'application/json'},content=body)
        assert response.status_code == 422
        assert response.json() == {'detail':'invalid job request'}
        assert client.get('/v1/jobs').json()['items'] == []


@pytest.mark.parametrize('unsupported', ['\x00', '\ud800', '\udfff'])
@pytest.mark.parametrize('in_key', [False, True])
def test_worker_invalid_json_result_records_safe_failure(database, unsupported, in_key):
    db,_ = database
    s,p = JobStore(db),principal()
    row = enqueue(s,p,max_attempts=1)
    result = {'nested':[{unsupported:'value'} if in_key else {'value':unsupported}]}
    assert Worker(s,p,{'echo':lambda payload:result},owner='test').run_once()
    stored = s.get(p,row['id'])
    assert stored['status'] == 'dead'
    assert stored['error_code'] == 'invalid_result'
    assert stored['result'] is None


@pytest.mark.parametrize('table', ['workflow_jobs','workflow_attempts'])
@pytest.mark.parametrize('privilege', ['SELECT','INSERT','UPDATE'])
def test_readiness_requires_each_runtime_table_privilege(database, table, privilege):
    db,_ = database
    class UnusedVerifier:
        def verify(self, authorization):
            raise AssertionError('readiness must not request credentials')
    with TestClient(create_workflow_app(db,UnusedVerifier())) as client:
        assert client.get('/readyz').status_code == 200
        with db.transaction(uuid4()) as conn:
            role = conn.execute('SELECT current_user').fetchone()[0]
            conn.execute(sql.SQL('REVOKE {} ON {} FROM {}').format(sql.SQL(privilege),sql.Identifier(table),sql.Identifier(role)))
        try:
            assert client.get('/readyz').status_code == 503
        finally:
            with db.transaction(uuid4()) as conn:
                conn.execute(sql.SQL('GRANT {} ON {} TO {}').format(sql.SQL(privilege),sql.Identifier(table),sql.Identifier(role)))
        assert client.get('/readyz').status_code == 200
