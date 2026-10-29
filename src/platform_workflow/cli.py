"""Explicit migration, API, and per-workspace worker entrypoints."""
import argparse
import os
import signal
import sys
import threading

from platform_backend_core import Database, MigrationRunner, ServiceTokenError, ServiceTokenSource, TokenVerifier

from .app import create_workflow_app
from .migrations import migrations
from .store import JobStore
from .worker import Worker


def verifier_from_env():
    def setting(primary, alias, required=False):
        value = os.environ.get(primary) or os.environ.get(alias)
        if required and not value:
            raise ValueError(f'{primary} is required')
        return value
    return TokenVerifier(issuer=setting('JWT_ISSUER','WORKFLOW_JWT_ISSUER',True),
                         audience=setting('JWT_AUDIENCE','WORKFLOW_JWT_AUDIENCE',True),
                         public_key=setting('JWT_PUBLIC_KEY','WORKFLOW_JWT_PUBLIC_KEY'),
                         jwks_url=setting('JWT_JWKS_URL','WORKFLOW_JWKS_URL'))


def worker_authorization_from_env(env=os.environ, *, post=None):
    """The worker's bearer token source: exactly one of a static value or an identity service client.

    A service client (``WORKFLOW_WORKER_CLIENT_ID`` + ``WORKFLOW_WORKER_PRIVATE_KEY_PEM``) gets a
    ``jobs:work`` token from ``<issuer>/v1/token`` and renews it before it expires; identity only
    accepts its own token endpoint as the assertion audience, so there is no separate token URL.
    """
    def value(name):
        return (env.get(name) or '').strip() or None
    static = value('WORKFLOW_WORKER_AUTHORIZATION')
    client_id, private_key = value('WORKFLOW_WORKER_CLIENT_ID'), value('WORKFLOW_WORKER_PRIVATE_KEY_PEM')
    if static and (client_id or private_key):
        raise ValueError('set WORKFLOW_WORKER_AUTHORIZATION or a worker service client, not both')
    if static:
        return lambda: static
    if not client_id or not private_key:
        raise ValueError('set WORKFLOW_WORKER_AUTHORIZATION, or WORKFLOW_WORKER_CLIENT_ID and WORKFLOW_WORKER_PRIVATE_KEY_PEM')
    issuer = value('JWT_ISSUER') or value('WORKFLOW_JWT_ISSUER')
    audience = value('JWT_AUDIENCE') or value('WORKFLOW_JWT_AUDIENCE')
    if not audience or not issuer:
        raise ValueError('a worker service client needs JWT_ISSUER and JWT_AUDIENCE')
    tokens = ServiceTokenSource(token_url=issuer.rstrip('/') + '/v1/token', client_id=client_id,
                                private_key_pem=private_key, audience=audience, scope='jobs:work', post=post)
    return tokens.authorization


def worker_principal(verifier, authorization):
    """The verified principal for this poll, or None when identity has no token for us right now."""
    try:
        return verifier.verify(authorization())
    except ServiceTokenError as error:
        print(f'worker token unavailable: {error}; retrying', file=sys.stderr)  # message never holds the token
        return None


def app_factory():
    db = Database(os.environ['DATABASE_URL'],max_size=int(os.environ.get('WORKFLOW_POOL_MAX','8')))
    return create_workflow_app(db,verifier_from_env())


def main():
    parser = argparse.ArgumentParser(prog='platform-workflow')
    parser.add_argument('command',choices=('migrate','api','worker'))
    args = parser.parse_args()
    if args.command == 'migrate':
        applied = MigrationRunner(os.environ['DATABASE_URL'],namespace='workflow').apply(migrations())
        print(f'applied {len(applied)} workflow migration(s)')
    elif args.command == 'api':
        import uvicorn
        uvicorn.run('platform_workflow.cli:app_factory',factory=True,host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
    else:
        # JWT is reverified before each poll, so expiry/revocation via configured issuer is honored.
        # No principal may be selected by an unverified workspace environment variable.
        verifier = verifier_from_env()
        authorization = worker_authorization_from_env()
        database = Database(os.environ['DATABASE_URL'],max_size=int(os.environ.get('WORKFLOW_POOL_MAX','2')))
        stop = threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        try:
            while not stop.is_set():
                principal = worker_principal(verifier, authorization)
                if principal is None:
                    stop.wait(30)
                    continue
                # Explicit built-in handler has no external effects. Embed Worker for application handlers.
                worker = Worker(JobStore(database),principal,{'health.noop':lambda payload: {'ok':True}},owner=os.environ.get('WORKFLOW_WORKER_OWNER','worker'),
                                queues=tuple(os.environ['WORKFLOW_WORKER_QUEUES'].split(',')))
                if not worker.run_once():
                    stop.wait(1)
        finally:
            database.close()
