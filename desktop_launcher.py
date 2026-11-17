"""Run one application batch from the desktop, with a lock and a saved log."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def acquire_lock(path: Path):
    handle = path.open('a+b')
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def child_environment() -> dict:
    env = os.environ.copy()
    node_dir = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin'
    env['PATH'] = str(node_dir) + os.pathsep + env.get('PATH', '')
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'
    return env


def needs_setup() -> bool:
    config_path = ROOT / 'config.json'
    if not config_path.exists():
        return True
    with config_path.open(encoding='utf-8') as handle:
        config = json.load(handle)
    if config.get('session', {}).get('nauk_at', '').strip():
        return False
    from dotenv import dotenv_values
    credentials = {**dotenv_values(ROOT / '.env'), **os.environ}
    return not (credentials.get('NAUKRI_USERNAME') and credentials.get('PASSWORD'))


def run_logged(arguments: list[str], env: dict) -> int:
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    log_path = logs / f'run-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
    print(f'Run log: {log_path}', flush=True)
    print('Press Ctrl+C to stop. Keep this window open while applying.', flush=True)
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(
            [sys.executable, '-u', 'apply_agent.py', *arguments],
            cwd=ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
        )
        try:
            for line in process.stdout:
                print(line, end='', flush=True)
                log.write(line)
                log.flush()
            return process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait()
            print('\nStopped by user.', flush=True)
            return 130


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check startup without login or applications')
    parser.add_argument('--setup', action='store_true', help='Update account/profile settings without applying')
    args = parser.parse_args()
    os.chdir(ROOT)
    env = child_environment()
    if not shutil.which('node', path=env['PATH']):
        print('Node.js was not found. Install Node.js and try again.')
        return 1
    lock = acquire_lock(ROOT / '.noperi-run.lock')
    if lock is None:
        print('NopeRi is already running. Use the existing application window.')
        return 1
    try:
        startup = subprocess.run(
            [sys.executable, 'apply_agent.py', '--help'], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
        )
        if startup.returncode:
            print(startup.stderr or startup.stdout)
            print('Startup failed. Run Finish-Setup.cmd in the NopeRi folder to repair dependencies.')
            return startup.returncode
        setup_required = needs_setup()
        if args.check:
            print('Startup check passed. Node.js and application imports are available.')
            print('Account setup needed.' if setup_required else 'Saved account credentials/session found.')
            print('No login or applications were attempted.')
            return 0
        if args.setup:
            print('Opening setup only. No application batch will be started.', flush=True)
            return run_logged(['--setup'], env)
        if setup_required:
            print('Complete the account setup in your browser. Applying will begin after you save and close it.')
        print('Starting one application batch with your saved search and profile settings.', flush=True)
        result = run_logged([], env)
        if result == 3:
            print('\nNaukri rejected the saved session. The batch stopped at the first authentication error.')
            print('Open Update Naukri Session on your Desktop, save fresh cookies, then start again.')
        elif result:
            print('\nRun stopped with an error. Review the output and saved log.')
            print('If your session expired, run Finish-Setup.cmd to update it.')
        else:
            print('\nBatch finished. Click the desktop entry again to run another batch.')
        return result
    finally:
        lock.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'NopeRi could not start: {error}')
        sys.exit(1)
