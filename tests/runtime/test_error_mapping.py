"""Only domain validation maps to 422; any other ValueError is a server bug (redacted 500).

Offline: the store is replaced or fails before touching the database.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from platform_backend_core import TokenVerifier
from test_jobs import principal

from platform_workflow import InvalidInput, JobStore
from platform_workflow.app import create_workflow_app


class _NoDatabase:
    def ready(self):
        return False

    def close(self):
        pass

    def transaction(self, workspace):
        raise AssertionError('database must not be reached')


@pytest.fixture
def client():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = TokenVerifier(issuer='https://test.invalid', audience='workflow', public_key=public)
    now = datetime.now(UTC)
    token = jwt.encode({'sub': str(uuid4()), 'ws': str(uuid4()), 'scope': 'jobs:read jobs:write jobs:work',
                        'iss': 'https://test.invalid', 'aud': 'workflow', 'iat': now, 'exp': now + timedelta(minutes=5),
                        'jti': str(uuid4())}, private, algorithm='RS256')
    with TestClient(create_workflow_app(_NoDatabase(), verifier)) as c:
        yield c, {'Authorization': 'Bearer ' + token, 'Idempotency-Key': 'key'}


def _store():
    return JobStore(_NoDatabase())


def _enqueue(**changes):
    args = {'key': 'key', 'queue': 'default', 'job_type': 'echo', 'payload': {'x': 1}} | changes
    return _store().enqueue(principal(), **args)


def test_invalid_input_is_a_value_error():
    assert issubclass(InvalidInput, ValueError)


@pytest.mark.parametrize('call', [
    lambda: _enqueue(key=''),
    lambda: _enqueue(queue='bad queue'),
    lambda: _enqueue(job_type='bad/type'),
    lambda: _enqueue(payload=[]),
    lambda: _enqueue(payload={'x': 'a' * 70000}),
    lambda: _enqueue(payload={'x': 'nul\x00'}),
    lambda: _enqueue(payload={'x': float('nan')}),
    lambda: _enqueue(max_attempts=101),
    lambda: _enqueue(run_at=datetime(2026, 10, 29)),
    lambda: _store().list(principal(), limit=0),
    lambda: _store().claim(principal(), owner='test', lease_seconds=0),
    lambda: _store().claim(principal(), owner='bad owner'),
    lambda: _store().claim(principal(), owner='test', queues=[]),
    lambda: _store().renew(principal(), uuid4(), lease_token=uuid4(), lease_seconds=901),
    lambda: _store().complete(principal(), uuid4(), lease_token=uuid4(), result={'x': 'a' * 20000}),
    lambda: _store().complete(principal(), uuid4(), lease_token=uuid4(), result=float('inf')),
    lambda: _store().fail(principal(), uuid4(), lease_token=uuid4(), error_code='private detail'),
])
def test_store_validation_raises_invalid_input(call):
    with pytest.raises(InvalidInput):
        call()


def test_pending_bound_stays_a_plain_value_error():
    # Service configuration, not client input: never 422.
    with pytest.raises(ValueError) as caught:
        JobStore(_NoDatabase(), max_pending=0)
    assert not isinstance(caught.value, InvalidInput)


def test_domain_validation_returns_422(client):
    c, h = client
    response = c.post('/v1/jobs', headers=h, json={'queue': 'bad queue', 'job_type': 'echo', 'payload': {}})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid job request'}
    # Python's JSON parser accepts NaN; the store refuses it as input, not as a server error.
    nan = b'{"queue":"default","job_type":"echo","payload":{"x":NaN}}'
    response = c.post('/v1/jobs', headers=h | {'Content-Type': 'application/json'}, content=nan)
    assert response.status_code == 422
    assert c.post('/v1/worker/claim', headers=h, json={'owner': 'bad owner'}).status_code == 422
    body = {'lease_token': str(uuid4()), 'error_code': 'private detail'}
    assert c.post(f'/v1/worker/jobs/{uuid4()}/fail', headers=h, json=body).status_code == 422


def test_unexpected_value_error_is_redacted_500(client, monkeypatch):
    def broken(self, principal, *, limit=50, after=None):
        raise ValueError('secret-bearing internal detail')

    monkeypatch.setattr(JobStore, 'list', broken)
    c, h = client
    response = c.get('/v1/jobs', headers=h)
    assert response.status_code == 500
    assert 'secret-bearing' not in response.text
    assert response.json() == {'detail': 'internal server error'}
