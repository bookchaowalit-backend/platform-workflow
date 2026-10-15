"""Bounded sequential worker. Applications inject a trusted handler registry."""
import threading
from .store import LeaseLost


class Worker:
    def __init__(self, store, principal, handlers, *, owner, queues=('default',), lease_seconds=30):
        self.store = store
        self.principal = principal
        self.handlers = dict(handlers)
        self.owner = owner
        self.queues = tuple(queues)
        self.lease_seconds = lease_seconds

    def run_once(self):
        job = self.store.claim(self.principal,owner=self.owner,queues=self.queues,lease_seconds=self.lease_seconds)
        if job is None:
            return False
        stop, lost = threading.Event(), threading.Event()
        def heartbeat():
            while not stop.wait(self.lease_seconds/3):
                try:
                    self.store.renew(self.principal,job['id'],lease_token=job['lease_token'],lease_seconds=self.lease_seconds)
                except Exception:
                    lost.set()
                    return
        thread = threading.Thread(target=heartbeat,name='workflow-heartbeat',daemon=True)
        thread.start()
        result, error = None, None
        try:
            handler = self.handlers.get(job['job_type'])
            if handler is None:
                error = 'unknown_handler'
            else:
                try:
                    result = handler(job['payload'])
                except Exception:
                    error = 'handler_failed'
        finally:
            stop.set()
            thread.join()
        if not lost.is_set():
            try:
                if error:
                    self.store.fail(self.principal,job['id'],lease_token=job['lease_token'],error_code=error)
                else:
                    self.store.complete(self.principal,job['id'],lease_token=job['lease_token'],result=result)
            except LeaseLost:
                pass
            except (ValueError, TypeError):
                self.store.fail(self.principal,job['id'],lease_token=job['lease_token'],error_code='invalid_result')
        return True
