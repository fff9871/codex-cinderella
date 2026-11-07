#!/usr/bin/env python3
"""Small synchronous Codex app-server JSON-RPC client (Python standard library)."""
import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time


def find_codex():
    """Resolve CLI even when a terminal/daemon has a minimal PATH."""
    override = os.environ.get('CODEX_BIN')
    if override:
        binary = Path(override).expanduser()
        if binary.is_file() and os.access(binary, os.X_OK):
            return str(binary.resolve())
        raise RuntimeError(f'CODEX_BIN is not an executable file: {override}')
    binary = shutil.which('codex')
    if binary:
        return binary
    for app_dir in (Path('/Applications'), Path.home() / 'Applications'):
        for relative in (
            'ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
            'Codex.app/Contents/Resources/codex',
            'Codex.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
        ):
            candidate = app_dir / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise RuntimeError('Codex CLI not found. Set CODEX_BIN to the absolute path of the codex executable.')


class Client:
    def __enter__(self):
        binary = find_codex()
        self.proc = subprocess.Popen([binary, 'app-server'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=None, bufsize=0)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.proc.stdout, selectors.EVENT_READ)
        self.buffer = b''
        self.counter = 0
        try:
            self.request('initialize', {'clientInfo': {'name': 'codex_cinderella', 'version': '1.0.0'},
                                        'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        payload = (json.dumps(message) + '\n').encode()
        # Use a buffered writer loop because stdin is unbuffered.
        while payload:
            written = self.proc.stdin.write(payload)
            if not written:
                raise RuntimeError('app-server stdin closed')
            payload = payload[written:]

    def request(self, method, params=None):
        self.counter += 1
        message = {'id': self.counter, 'method': method}
        if params is not None:
            message['params'] = params
        self.send(message)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if b'\n' not in self.buffer:
                if not self.sel.select(max(0, deadline - time.monotonic())):
                    break
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('app-server closed the connection')
                self.buffer += chunk
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                response = json.loads(line)
                if response.get('id') == self.counter:
                    if 'error' in response:
                        raise RuntimeError(json.dumps(response['error'], ensure_ascii=False))
                    return response['result']
        raise TimeoutError(f'Timed out waiting for {method}')

    def __exit__(self, *_):
        self.sel.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consume', action='store_true')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--credit-id')
    parser.add_argument('--details', action='store_true')
    args = parser.parse_args()
    if args.consume and not args.idempotency_key:
        parser.error('--consume requires --idempotency-key')
    with Client() as client:
        if args.consume:
            params = {'idempotencyKey': args.idempotency_key}
            if args.credit_id:
                params['creditId'] = args.credit_id
            result = client.request('account/rateLimitResetCredit/consume', params)
            print(json.dumps({'redemption': result}, ensure_ascii=False))
        print(json.dumps(client.request('account/rateLimits/read', {
            'excludeResetCreditDetails': not args.details}), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
