"""Command-scoped browser reuse over private child-process pipes, not a server."""
from contextlib import contextmanager
import json
import os
from queue import Queue, Empty
import subprocess
import sys
from threading import Thread, local

from .core import VpubError

_LOCAL = local()


class Session:
    def __init__(self, platform, account):
        self.platform, self.account = platform, account
        self.process = None
        self.responses = Queue()
        self.failed = False

    def call(self, payload):
        if self.failed:
            raise VpubError('browser_session_closed', 'The interrupted session cannot replay a command.')
        if (payload['platform'], payload['account'], payload['action']) != (self.platform, self.account, 'collections'):
            raise VpubError('session_scope', 'This session is bound to one account and collection operations.')
        if self.process is None:
            from .browser_broker import enabled, release
            if enabled():
                release(self.platform, self.account)
            self.process = subprocess.Popen([sys.executable, '-m', 'video_publisher.browser_worker', '--session'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', bufsize=1, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            def read():
                for line in self.process.stdout:
                    self.responses.put(line)
                self.responses.put(None)
            Thread(target=read, daemon=True).start()
        try:
            self.process.stdin.write(json.dumps(payload) + '\n')
            self.process.stdin.flush()
            line = self.responses.get(timeout=600)
            result = json.loads(line) if line else None
            if not isinstance(result, dict):
                raise ValueError('response')
        except (Empty, ValueError, OSError):
            self.failed = True
            self.close()
            raise VpubError('browser_session_interrupted', 'Browser session interrupted; inspect the saved receipt before retrying.') from None
        if not result.get('ok'):
            error = result['error']
            raise VpubError(error['code'], error['message'], error.get('hint', ''))
        return result['data']

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=5)
            finally:
                self.process.stdout.close()


@contextmanager
def reuse(platform, account='default'):
    previous = getattr(_LOCAL, 'session', None)
    if previous is not None:
        if (previous.platform, previous.account) != (platform, account):
            raise VpubError('session_scope', 'Cannot change accounts in a browser transaction.')
        yield previous
        return
    session = Session(platform, account)
    _LOCAL.session = session
    try:
        yield session
    finally:
        session.close()
        _LOCAL.session = previous


def current():
    return getattr(_LOCAL, 'session', None)
