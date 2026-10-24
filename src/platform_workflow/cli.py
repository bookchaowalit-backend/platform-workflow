"""Explicit migration, API, and per-workspace worker entrypoints."""
import argparse
import os
import signal
import threading

from platform_backend_core import Database, MigrationRunner, TokenVerifier

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
        database = Database(os.environ['DATABASE_URL'],max_size=int(os.environ.get('WORKFLOW_POOL_MAX','2')))
        stop = threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:stop.set())
        try:
            while not stop.is_set():
                principal = verifier.verify(os.environ['WORKFLOW_WORKER_AUTHORIZATION'])
                # Explicit built-in handler has no external effects. Embed Worker for application handlers.
                worker = Worker(JobStore(database),principal,{'health.noop':lambda payload: {'ok':True}},owner=os.environ.get('WORKFLOW_WORKER_OWNER','worker'),
                                queues=tuple(os.environ['WORKFLOW_WORKER_QUEUES'].split(',')))
                if not worker.run_once():
                    stop.wait(1)
        finally:
            database.close()
