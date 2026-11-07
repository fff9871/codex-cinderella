#!/usr/bin/env python3
"""Codex Cinderella: redeem expiring Codex reset credits before midnight."""
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT / '.cinderella'


def rpc(method, params=None):
    # A short-lived client keeps daemon connections and auth refreshes simple.
    from codex_quota import Client
    with Client() as client:
        return client.request(method, params)


def save(path, state):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as file:
        json.dump(state, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)


def check(state, persist, request=rpc, now=None, lead=3600, dry_run=False):
    now = time.time() if now is None else now
    usage = request('account/rateLimits/read', {'excludeResetCreditDetails': False})
    summary = usage.get('rateLimitResetCredits') or {}
    credits = summary.get('credits')
    if credits is None:
        raise RuntimeError('Card expiry details unavailable; will retry without redeeming blindly')
    # The backend may cap detail rows. Refresh after every redemption.
    due = sorted((c for c in credits if c.get('status') == 'available'
                  and c.get('resetType') == 'codexRateLimits'
                  and isinstance(c.get('expiresAt'), (int, float))
                  and now < c['expiresAt'] <= now + lead
                  and not state.get(c['id'], {}).get('done')),
                 key=lambda c: c['expiresAt'])
    logging.info('Available cards: %s; due within %ss: %s', summary.get('availableCount'), lead, len(due))
    if not due:
        return
    card = due[0]
    cid = card['id']
    if dry_run:
        logging.info('DRY RUN: would redeem card %s expiring at %s', cid, card['expiresAt'])
        return
    attempt = state.setdefault(cid, {'key': str(uuid.uuid4())})
    persist(state)  # Persist BEFORE any network mutation, including retries after a crash.
    result = request('account/rateLimitResetCredit/consume',
                     {'creditId': cid, 'idempotencyKey': attempt['key']})
    outcome = result.get('outcome')
    logging.info('Redemption result for %s: %s', cid, outcome)
    if outcome in ('reset', 'alreadyRedeemed', 'noCredit'):
        attempt['done'] = True
    elif outcome == 'nothingToReset':
        # This attempt definitively consumed nothing; try a fresh attempt next poll.
        attempt['key'] = str(uuid.uuid4())
    else:
        raise RuntimeError(f'Unknown redemption outcome: {outcome}; preserving retry key')
    persist(state)
    refreshed = request('account/rateLimits/read', {'excludeResetCreditDetails': True})
    logging.info('After redemption: %s', json.dumps(refreshed.get('rateLimits'), ensure_ascii=False))


def lock_file(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return (directory / 'daemon.pid').open('a+')


def running_pid(file):
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        file.seek(0)
        value = file.read().strip()
        return int(value) if value else None
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['start', 'run', 'stop', 'status', 'once'])
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    p.add_argument('--interval', type=int, default=60, help='Poll interval in seconds (default: 60)')
    p.add_argument('--lead-seconds', type=int, default=3600)
    p.add_argument('--dry-run', action='store_true', help='Inspect only; never consume a card')
    args = p.parse_args()
    if args.interval <= 0 or args.lead_seconds <= 0:
        p.error('interval and lead-seconds must be positive')
    directory = args.state_dir.resolve()
    with lock_file(directory) as lock:
        pid = running_pid(lock)
        if args.command in ('stop', 'status'):
            if pid and args.command == 'stop':
                os.kill(pid, signal.SIGTERM)
                print(f'Stop requested for PID {pid}')
            else:
                print(f'Running: PID {pid}' if pid else 'Not running')
            return
        if pid:
            p.error(f'Already running: PID {pid}')
        if args.command == 'start':
            from codex_quota import find_codex
            # Resolve before detaching so configuration errors appear in the terminal.
            env = dict(os.environ, CODEX_BIN=find_codex())
            fcntl.flock(lock, fcntl.LOCK_UN)
            cmd = [sys.executable, str(Path(__file__).resolve()), 'run', '--state-dir', str(directory),
                   '--interval', str(args.interval), '--lead-seconds', str(args.lead_seconds)]
            if args.dry_run:
                cmd.append('--dry-run')
            with (directory / 'launcher.log').open('a') as output:
                child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                         start_new_session=True, cwd=ROOT, env=env)
            for _ in range(50):
                time.sleep(0.1)
                if child.poll() is not None:
                    p.error(f'Failed to start; see {directory / "launcher.log"}')
                if running_pid(lock) == child.pid:
                    print(f'Started PID {child.pid}; logs: {directory / "cinderella.log"}')
                    return
                fcntl.flock(lock, fcntl.LOCK_UN)
            p.error('Startup not confirmed; inspect status and launcher.log')
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        handlers = [RotatingFileHandler(directory / 'cinderella.log', maxBytes=2_000_000, backupCount=3),
                    logging.StreamHandler()]
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=handlers)
        path = directory / 'state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        stopping = False

        def stop(*_):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        logging.info('Cinderella started; dry_run=%s', args.dry_run)
        while not stopping:
            try:
                check(state, lambda value: save(path, value), lead=args.lead_seconds, dry_run=args.dry_run)
            except Exception:
                logging.exception('Check failed; retrying on next poll')
                if args.command == 'once':
                    sys.exit(1)
            if args.command == 'once':
                break
            deadline = time.monotonic() + args.interval
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        logging.info('Cinderella stopped')


if __name__ == '__main__':
    main()
