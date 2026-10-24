"""Real PostgreSQL behavioral evidence; missing DSN fails instead of skipping."""
import concurrent.futures
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from platform_backend_core import Database, MigrationRunner, Principal
from psycopg import sql
from psycopg.conninfo import make_conninfo

from platform_workflow import Conflict, JobStore, LeaseLost, NotFound, PermissionDenied, migrations


def principal(workspace=None, scopes=('jobs:read', 'jobs:write', 'jobs:work')):
    return Principal(uuid4(), workspace or uuid4(), frozenset(scopes))


@pytest.fixture
def database():
    dsn = os.environ['PLATFORM_TEST_DSN']
    schema = 'workflow_test_' + uuid4().hex
    role_name = schema + '_role'
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(role_name)))
        conn.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(sql.Identifier(schema), sql.Identifier(role_name)))
    isolated = make_conninfo(dsn, user=role_name, options=f'-c search_path={schema}')
    with psycopg.connect(isolated) as conn:
        assert conn.execute('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone() == (False, False)
    runner = MigrationRunner(isolated, namespace='workflow')
    assert runner.apply(migrations()) == ['001_workflow']
    assert runner.apply(migrations()) == []
    db = Database(isolated, max_size=4)
    yield db, isolated
    db.close()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role_name)))


def enqueue(store, p, key=None, **kw):
    return store.enqueue(p, key=key or str(uuid4()), queue='default', job_type='echo', payload={'x': 1}, **kw)


def test_idempotency_conflict_and_workspace_isolation(database):
    db, _ = database
    s, a, b = JobStore(db), principal(), principal()
    first = enqueue(s, a, 'same')
    assert enqueue(s, a, 'same')['id'] == first['id']
    with pytest.raises(Conflict):
        s.enqueue(a, key='same', queue='default', job_type='echo', payload={'x': 2})
    assert enqueue(s, b, 'same')['id'] != first['id']
    assert s.list(b)[0]['workspace_id'] == b.workspace
    with pytest.raises(NotFound):
        s.get(b, first['id'])
    with pytest.raises(NotFound):
        s.cancel(b, first['id'])
    with db.transaction(b.workspace) as c:
        assert c.execute('SELECT count(*) FROM workflow_jobs WHERE id=%s', (first['id'],)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('INSERT INTO workflow_jobs (workspace_id,queue,job_type,payload,idempotency_key,fingerprint) VALUES (%s,\'default\',\'echo\',\'{}\',\'forged\',\'bad\')', (a.workspace,))


def _claim_process(dsn, workspace, start, out):
    db = Database(dsn, max_size=1)
    try:
        start.wait(10)
        job = JobStore(db).claim(principal(workspace), owner='process')
        out.put(str(job['id']) if job else None)
    finally:
        db.close()


def test_distinct_claims_in_independent_processes(database):
    db, dsn = database
    s, p = JobStore(db), principal()
    expected = {str(enqueue(s, p)['id']) for _ in range(2)}
    context = multiprocessing.get_context('spawn')
    start, out = context.Event(), context.Queue()
    processes = [context.Process(target=_claim_process, args=(dsn, p.workspace, start, out)) for _ in range(2)]
    for proc in processes:
        proc.start()
    start.set()
    results = [out.get(timeout=20) for _ in processes]
    for proc in processes:
        proc.join(20)
        assert proc.exitcode == 0
    assert set(results) == expected


def expire(db, p, job):
    with db.transaction(p.workspace) as conn:
        conn.execute("UPDATE workflow_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (job['id'],))


def test_expired_lease_recovers_and_fences_all_mutations(database):
    db, _ = database
    s, p = JobStore(db), principal()
    enqueue(s, p)
    old = s.claim(p, owner='old')
    expire(db, p, old)
    for action in (lambda: s.renew(p, old['id'], lease_token=old['lease_token']),
                   lambda: s.complete(p, old['id'], lease_token=old['lease_token'], result={}),
                   lambda: s.fail(p, old['id'], lease_token=old['lease_token'], error_code='failed')):
        with pytest.raises(LeaseLost):
            action()
    new = s.claim(p, owner='new')
    assert new['attempts'] == 2
    assert new['lease_token'] != old['lease_token']
    with pytest.raises(LeaseLost):
        s.complete(p, old['id'], lease_token=old['lease_token'], result={})
    assert s.renew(p, new['id'], lease_token=new['lease_token'])['status'] == 'running'
    completed = s.complete(p, new['id'], lease_token=new['lease_token'], result={'ok': True})
    assert completed['status'] == 'succeeded'
    assert s.complete(p, new['id'], lease_token=new['lease_token'], result={'ok': True})['id'] == new['id']
    with pytest.raises(Conflict):
        s.complete(p, new['id'], lease_token=new['lease_token'], result={'ok': False})


def test_scheduling_backoff_dead_letter_and_cancellation(database):
    db, _ = database
    s, p = JobStore(db), principal()
    future = enqueue(s, p, run_at=datetime.now(UTC)+timedelta(days=1))
    assert s.claim(p, owner='test') is None
    assert s.cancel(p, future['id'])['status'] == 'cancelled'
    assert s.cancel(p, future['id'])['status'] == 'cancelled'
    enqueue(s, p, max_attempts=2)
    job = s.claim(p, owner='test')
    retried = s.fail(p, job['id'], lease_token=job['lease_token'], error_code='temporary')
    assert retried['status'] == 'queued'
    assert retried['run_at'] > retried['updated_at']
    assert s.claim(p, owner='test') is None
    with db.transaction(p.workspace) as conn:
        conn.execute("UPDATE workflow_jobs SET run_at=clock_timestamp()-interval '1 second' WHERE id=%s", (job['id'],))
    job = s.claim(p, owner='test')
    dead = s.fail(p, job['id'], lease_token=job['lease_token'], error_code='temporary')
    assert dead['status'] == 'dead'
    assert len(s.attempts(p, job['id'])) == 2
    with pytest.raises(Conflict):
        s.cancel(p, job['id'])
    running = enqueue(s, p)
    job = s.claim(p, owner='test')
    assert s.cancel(p, running['id'])['status'] == 'cancelled'
    with pytest.raises(LeaseLost):
        s.complete(p, job['id'], lease_token=job['lease_token'], result={})


def test_expired_final_attempt_is_dead_letter(database):
    db, _ = database
    s, p = JobStore(db), principal()
    row = enqueue(s, p, max_attempts=1)
    job = s.claim(p, owner='test')
    expire(db, p, job)
    assert s.claim(p, owner='other') is None
    assert s.get(p, row['id'])['status'] == 'dead'


def test_bounds_and_scope_fail_before_work(database):
    db, _ = database
    s, p = JobStore(db), principal(scopes=())
    with pytest.raises(PermissionDenied):
        enqueue(s, p)
    p = principal()
    with pytest.raises(ValueError):
        s.enqueue(p, key='key', queue='default', job_type='echo', payload={'x': 'a'*70000})
    with pytest.raises(ValueError):
        enqueue(s, p, max_attempts=101)
    with pytest.raises(ValueError):
        s.claim(p, owner='test', lease_seconds=0)


def test_concurrent_enqueue_once_backlog_bound_and_missing_context(database):
    db, _ = database
    p, other = principal(), principal()
    s = JobStore(db,max_pending=1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: enqueue(s,p,'concurrent'), range(4)))
    assert len({row['id'] for row in rows}) == 1
    with pytest.raises(Conflict):
        enqueue(s,p)
    assert enqueue(s,other)
    with psycopg.connect(database[1]) as conn:
        assert conn.execute('SELECT count(*) FROM workflow_jobs').fetchone() == (0,)
        assert conn.execute('SELECT count(*) FROM workflow_attempts').fetchone() == (0,)
    job = s.claim(p,owner='test')
    with db.transaction(other.workspace) as conn:
        assert conn.execute('SELECT count(*) FROM workflow_attempts').fetchone()[0] == 0
    assert s.list(p,limit=1)[0]['id'] == job['id']
    assert s.list(p,after=job['id']) == []


def test_jsonb_expansion_is_validated_before_inserting(database):
    db,_ = database
    s,p = JobStore(db),principal()
    # Compact JSON fits 60k, but PostgreSQL JSONB spaces expand this past 65k.
    with pytest.raises(ValueError):
        s.enqueue(p,key='expanded',queue='default',job_type='echo',payload={'x':[0]*25000})
    assert s.list(p) == []


def test_scientific_notation_completion_replay_is_idempotent(database):
    db,_ = database
    s,p = JobStore(db),principal()
    enqueue(s,p)
    job = s.claim(p,owner='test')
    result = {'n':1e20,'nested':[1e-20,{'whole':1.0}]}
    first = s.complete(p,job['id'],lease_token=job['lease_token'],result=result)
    repeated = s.complete(p,job['id'],lease_token=job['lease_token'],result=result)
    assert repeated['id'] == first['id']
    with pytest.raises(Conflict):
        s.complete(p,job['id'],lease_token=job['lease_token'],result={'n':2e20})


def test_literal_backslash_unicode_escape_is_valid_payload_and_result(database):
    db,_ = database
    s,p = JobStore(db),principal()
    literal = r'\u0000 and \ud800'
    row = s.enqueue(p,key='literal',queue='default',job_type='echo',payload={literal:[literal]})
    job = s.claim(p,owner='test')
    result = s.complete(p,job['id'],lease_token=job['lease_token'],result={literal:literal})
    assert s.get(p,row['id'])['payload'] == {literal:[literal]}
    assert result['result'] == {literal:literal}
