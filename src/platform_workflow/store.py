"""Durable at-least-once jobs with transaction locks and lease fencing."""
import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class LeaseLost(Conflict):
    pass


class PermissionDenied(Exception):
    pass


def _scope(principal, required):
    if required not in principal.scopes:
        raise PermissionDenied(required)
    if not isinstance(principal.workspace, UUID):
        raise PermissionDenied('verified workspace required')


def _json(value, limit):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    # PostgreSQL JSONB rejects actual NUL and surrogate code points in both
    # object keys and values. Inspect decoded strings, preserving literal escapes.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if any(character == '\x00' or 0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise ValueError('JSON contains unsupported Unicode')
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    if len(encoded.encode()) > limit:
        raise ValueError('JSON exceeds size limit')
    return encoded


def _name(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', value):
        raise ValueError(f'invalid {label}')
    return value


class JobStore:
    def __init__(self, database, *, max_pending=10000):
        if not 1 <= max_pending <= 100000:
            raise ValueError('invalid pending bound')
        self.database = database
        self.max_pending = max_pending

    @staticmethod
    def _database_json_bound(c, value, limit):
        # JSONB adds whitespace and expands numeric exponents: bound its real form too.
        c.execute('SELECT octet_length(%s::jsonb::text) AS size', (Jsonb(value),))
        if c.fetchone()['size'] > limit:
            raise ValueError('stored JSON exceeds size limit')

    def enqueue(self, principal, *, key, queue, job_type, payload, run_at=None, max_attempts=3):
        _scope(principal, 'jobs:write')
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError('invalid idempotency key')
        _name(queue, 'queue')
        _name(job_type, 'job type')
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        _json(payload, 60000)  # margin for PostgreSQL JSONB textual whitespace
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError('invalid max_attempts')
        if run_at is not None:
            if not isinstance(run_at, datetime) or run_at.tzinfo is None:
                raise ValueError('run_at must include timezone')
            run_at = run_at.astimezone(UTC)
        fingerprint = hashlib.sha256(_json({'queue':queue,'type':job_type,'payload':payload,
            'run_at': run_at.isoformat() if run_at else None,'max_attempts':max_attempts}, 65536).encode()).hexdigest()
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            self._database_json_bound(c, payload, 65536)
            # Bounded backlog admission and duplicate handling share a workspace lock.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,17))', (str(principal.workspace),))
            c.execute("SELECT * FROM workflow_jobs WHERE workspace_id=%s AND operation='enqueue' AND idempotency_key=%s", (principal.workspace,key))
            prior = c.fetchone()
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise Conflict('idempotency key payload mismatch')
                return prior
            c.execute("SELECT count(*) AS count FROM workflow_jobs WHERE status IN ('queued','running')")
            if c.fetchone()['count'] >= self.max_pending:
                raise Conflict('workspace pending limit reached')
            c.execute('''INSERT INTO workflow_jobs(workspace_id,idempotency_key,fingerprint,queue,job_type,payload,run_at,max_attempts)
                VALUES(%s,%s,%s,%s,%s,%s,COALESCE(%s,clock_timestamp()),%s) RETURNING *''',
                (principal.workspace,key,fingerprint,queue,job_type,Jsonb(payload),run_at,max_attempts))
            return c.fetchone()

    def get(self, principal, job_id):
        _scope(principal, 'jobs:read')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            return self._get(c, job_id)

    @staticmethod
    def _get(c, job_id, *, lock=False):
        c.execute('SELECT * FROM workflow_jobs WHERE id=%s' + (' FOR UPDATE' if lock else ''), (job_id,))
        row = c.fetchone()
        if row is None:
            raise NotFound('job not found')
        return row

    def list(self, principal, *, limit=50, after=None):
        _scope(principal, 'jobs:read')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            c.execute('SELECT * FROM workflow_jobs WHERE (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s', (after,after,limit))
            return c.fetchall()

    def attempts(self, principal, job_id):
        _scope(principal, 'jobs:read')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            self._get(c, job_id)
            c.execute('SELECT * FROM workflow_attempts WHERE job_id=%s ORDER BY attempt', (job_id,))
            return c.fetchall()

    def cancel(self, principal, job_id):
        _scope(principal, 'jobs:write')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            row = self._get(c, job_id, lock=True)
            if row['status'] == 'cancelled':
                return row
            if row['status'] in ('succeeded','dead'):
                raise Conflict('terminal job cannot be cancelled')
            c.execute("UPDATE workflow_attempts SET outcome='cancelled',finished_at=clock_timestamp() WHERE job_id=%s AND finished_at IS NULL", (job_id,))
            c.execute("UPDATE workflow_jobs SET status='cancelled',lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s RETURNING *", (job_id,))
            return c.fetchone()

    @staticmethod
    def _lease_seconds(seconds):
        if type(seconds) is not int or not 1 <= seconds <= 900:
            raise ValueError('lease_seconds must be 1..900')

    def claim(self, principal, *, owner, queues=('default',), lease_seconds=30):
        _scope(principal, 'jobs:work')
        self._lease_seconds(lease_seconds)
        _name(owner, 'owner')
        if not queues or len(queues)>20:
            raise ValueError('queues must contain 1..20 entries')
        for queue in queues:
            _name(queue, 'queue')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            # Terminalize exhausted leases without locking jobs held by other workers.
            c.execute('''SELECT id FROM workflow_jobs WHERE status='running' AND lease_until<=clock_timestamp()
                AND attempts>=max_attempts AND queue=ANY(%s) FOR UPDATE SKIP LOCKED''', (list(queues),))
            exhausted = [r['id'] for r in c.fetchall()]
            if exhausted:
                c.execute("UPDATE workflow_attempts SET outcome='expired',finished_at=clock_timestamp() WHERE job_id=ANY(%s) AND finished_at IS NULL", (exhausted,))
                c.execute("UPDATE workflow_jobs SET status='dead',lease_until=NULL,error_code='lease_expired',updated_at=clock_timestamp() WHERE id=ANY(%s)", (exhausted,))
            c.execute('''SELECT * FROM workflow_jobs WHERE queue=ANY(%s) AND attempts<max_attempts AND
                ((status='queued' AND run_at<=clock_timestamp()) OR (status='running' AND lease_until<=clock_timestamp()))
                ORDER BY run_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1''', (list(queues),))
            row = c.fetchone()
            if not row:
                return None
            c.execute("UPDATE workflow_attempts SET outcome='expired',finished_at=clock_timestamp() WHERE job_id=%s AND finished_at IS NULL", (row['id'],))
            token = uuid4()
            c.execute('''UPDATE workflow_jobs SET status='running',attempts=attempts+1,lease_owner=%s,lease_token=%s,
                lease_until=clock_timestamp()+%s*interval '1 second',updated_at=clock_timestamp() WHERE id=%s RETURNING *''',
                (owner,token,lease_seconds,row['id']))
            claimed = c.fetchone()
            c.execute('INSERT INTO workflow_attempts(workspace_id,job_id,attempt,lease_token,owner) VALUES (%s,%s,%s,%s,%s)',
                      (principal.workspace,row['id'],claimed['attempts'],token,owner))
            return claimed

    @staticmethod
    def _fenced(c, job_id, lease_token):
        # Evaluate the database clock after acquiring the row lock.
        row = JobStore._get(c, job_id, lock=True)
        c.execute('SELECT clock_timestamp() AS now')
        now = c.fetchone()['now']
        if row['status'] != 'running' or row['lease_token'] != lease_token or row['lease_until'] <= now:
            raise LeaseLost('lease expired, replaced or job terminal')
        return row

    def renew(self, principal, job_id, *, lease_token, lease_seconds=30):
        _scope(principal, 'jobs:work')
        self._lease_seconds(lease_seconds)
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            self._fenced(c, job_id, lease_token)
            c.execute("UPDATE workflow_jobs SET lease_until=clock_timestamp()+%s*interval '1 second',updated_at=clock_timestamp() WHERE id=%s RETURNING *", (lease_seconds,job_id))
            return c.fetchone()

    def complete(self, principal, job_id, *, lease_token, result):
        _scope(principal, 'jobs:work')
        _json(result, 14000)
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            self._database_json_bound(c, result, 16384)
            row = self._get(c, job_id, lock=True)
            if row['status']=='succeeded' and row['lease_token']==lease_token:
                # Compare the stored representation inside PostgreSQL: JSONB
                # normalizes exponent notation, unlike Python's JSON encoder.
                c.execute('SELECT result = %s::jsonb AS same FROM workflow_jobs WHERE id=%s', (Jsonb(result),job_id))
                if not c.fetchone()['same']:
                    raise Conflict('completion result mismatch')
                return row
            self._fenced(c, job_id, lease_token)
            c.execute("UPDATE workflow_attempts SET outcome='succeeded',finished_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s", (job_id,lease_token))
            c.execute("UPDATE workflow_jobs SET status='succeeded',result=%s,lease_until=NULL,error_code=NULL,updated_at=clock_timestamp() WHERE id=%s RETURNING *", (Jsonb(result),job_id))
            return c.fetchone()

    def fail(self, principal, job_id, *, lease_token, error_code):
        _scope(principal, 'jobs:work')
        _name(error_code, 'safe error code')
        with self.database.transaction(principal.workspace) as conn, conn.cursor(row_factory=dict_row) as c:
            self._fenced(c, job_id, lease_token)
            c.execute("UPDATE workflow_attempts SET outcome='failed',error_code=%s,finished_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s", (error_code,job_id,lease_token))
            c.execute('''UPDATE workflow_jobs SET status=CASE WHEN attempts>=max_attempts THEN 'dead' ELSE 'queued' END,
                run_at=clock_timestamp()+LEAST(3600,power(2,LEAST(attempts,12))) * interval '1 second',
                lease_until=NULL,error_code=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING *''', (error_code,job_id))
            return c.fetchone()
