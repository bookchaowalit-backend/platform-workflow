"""Explicit migration, API, and per-workspace worker entrypoints."""
import argparse
import os
import signal
import threading

from platform_backend_core import Database, MigrationRunner, ServiceTokenSource, TokenVerifier

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
    ``jobs:work`` token from identity's client_credentials grant and renews it before it expires.
    """
    static = env.get('WORKFLOW_WORKER_AUTHORIZATION')
    client_id, private_key = env.get('WORKFLOW_WORKER_CLIENT_ID'), env.get('WORKFLOW_WORKER_PRIVATE_KEY_PEM')
    if static and (client_id or private_key):
        raise ValueError('set WORKFLOW_WORKER_AUTHORIZATION or a worker service client, not both')
    if static:
        return lambda: static
    if not client_id or not private_key:
        raise ValueError('set WORKFLOW_WORKER_AUTHORIZATION, or WORKFLOW_WORKER_CLIENT_ID and WORKFLOW_WORKER_PRIVATE_KEY_PEM')
    issuer = env.get('JWT_ISSUER') or env.get('WORKFLOW_JWT_ISSUER')
    audience = env.get('JWT_AUDIENCE') or env.get('WORKFLOW_JWT_AUDIENCE')
    token_url = env.get('WORKFLOW_WORKER_TOKEN_URL') or (issuer.rstrip('/') + '/v1/token' if issuer else None)
    if not audience or not token_url:
        raise ValueError('a worker service client needs JWT_AUDIENCE and JWT_ISSUER (or WORKFLOW_WORKER_TOKEN_URL)')
    tokens = ServiceTokenSource(token_url=token_url, client_id=client_id, private_key_pem=private_key,
                                audience=audience, scope='jobs:work', post=post)
    return tokens.authorization


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
                principal = verifier.verify(authorization())
                # Explicit built-in handler has no external effects. Embed Worker for application handlers.
                worker = Worker(JobStore(database),principal,{'health.noop':lambda payload: {'ok':True}},owner=os.environ.get('WORKFLOW_WORKER_OWNER','worker'),
                                queues=tuple(os.environ['WORKFLOW_WORKER_QUEUES'].split(',')))
                if not worker.run_once():
                    stop.wait(1)
        finally:
            database.close()
