"""Where the CLI worker gets its bearer token: a static value, or identity's client_credentials grant."""
import json
import urllib.parse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from platform_backend_core import ServiceTokenError

from platform_workflow.cli import worker_authorization_from_env, worker_principal

ISSUER = 'https://identity.test'


@pytest.fixture(scope='module')
def private_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def client_env(private_pem, **extra):
    return {'JWT_ISSUER': ISSUER, 'JWT_AUDIENCE': 'workflow-api', 'WORKFLOW_WORKER_CLIENT_ID': 'workflow-worker',
            'WORKFLOW_WORKER_PRIVATE_KEY_PEM': private_pem, **extra}


def test_static_authorization_is_used_as_is():
    authorization = worker_authorization_from_env({'WORKFLOW_WORKER_AUTHORIZATION': 'Bearer abc'})
    assert authorization() == 'Bearer abc'


def test_client_credentials_ask_identity_for_a_jobs_work_token(private_pem):
    calls = []

    def post(url, form, timeout):
        calls.append((url, urllib.parse.parse_qs(form.decode())))
        return 200, json.dumps({'access_token': 'service-token', 'token_type': 'Bearer', 'expires_in': 600}).encode()

    authorization = worker_authorization_from_env(client_env(private_pem), post=post)
    assert authorization() == 'Bearer service-token'
    assert authorization() == 'Bearer service-token'
    assert len(calls) == 1, 'the token is cached until it needs renewing'
    url, form = calls[0]
    assert url == ISSUER + '/v1/token'
    assert (form['audience'], form['scope'], form['client_id']) == (['workflow-api'], ['jobs:work'], ['workflow-worker'])


def test_token_url_and_audience_aliases(private_pem):
    calls = []

    def post(url, form, timeout):
        calls.append((url, urllib.parse.parse_qs(form.decode())))
        return 200, b'{"access_token":"t","token_type":"Bearer","expires_in":600}'

    env = client_env(private_pem)
    env.pop('JWT_AUDIENCE')
    env.pop('JWT_ISSUER')
    env |= {'WORKFLOW_JWT_AUDIENCE': 'workflow', 'WORKFLOW_JWT_ISSUER': ISSUER + '/'}
    worker_authorization_from_env(env, post=post)()
    assert calls[0][0] == ISSUER + '/v1/token', 'the assertion audience is the issuer token endpoint'
    assert calls[0][1]['audience'] == ['workflow']


@pytest.mark.parametrize('env', [
    {},
    {'WORKFLOW_WORKER_AUTHORIZATION': 'Bearer abc', 'WORKFLOW_WORKER_CLIENT_ID': 'workflow-worker'},
    {'WORKFLOW_WORKER_AUTHORIZATION': '   '},
    {'WORKFLOW_WORKER_CLIENT_ID': ' ', 'WORKFLOW_WORKER_PRIVATE_KEY_PEM': ' ', 'JWT_ISSUER': ISSUER,
     'JWT_AUDIENCE': 'workflow-api'},
    {'WORKFLOW_WORKER_CLIENT_ID': 'workflow-worker', 'JWT_ISSUER': ISSUER, 'JWT_AUDIENCE': 'workflow-api'},
    {'WORKFLOW_WORKER_PRIVATE_KEY_PEM': 'x', 'JWT_ISSUER': ISSUER, 'JWT_AUDIENCE': 'workflow-api'},
])
def test_exactly_one_complete_source_is_required(env):
    with pytest.raises(ValueError):
        worker_authorization_from_env(env)


def test_errors_never_contain_the_key(private_pem):
    broken = '-----BEGIN PRIVATE KEY-----\nc2VjcmV0LW1hdGVyaWFs\n-----END PRIVATE KEY-----\n'
    with pytest.raises(ValueError) as caught:
        worker_authorization_from_env(client_env(broken))
    assert 'c2VjcmV0' not in str(caught.value) and 'PRIVATE KEY' not in str(caught.value)
    assert caught.value.__cause__ is None


class Verifier:
    def verify(self, authorization):
        return ('principal', authorization)


def test_worker_principal_verifies_the_current_token():
    assert worker_principal(Verifier(), lambda: 'Bearer t') == ('principal', 'Bearer t')


def test_worker_principal_skips_a_poll_when_identity_has_no_token(capsys):
    def unavailable():
        raise ServiceTokenError('identity is unreachable')
    assert worker_principal(Verifier(), unavailable) is None
    assert 'identity is unreachable' in capsys.readouterr().err
