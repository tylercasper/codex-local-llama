#!/usr/bin/env python3
"""Disposable Ubuntu VM acceptance. Run after guest-install.sh, as desktop user.

python3 acceptance.py cli|gui --output-dir PATH
GUI checks require DISPLAY (or WAYLAND_DISPLAY), python3-websocket, and a logged-in
Ubuntu desktop. This creates/removes a temporary project and model-written files.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.request
import uuid


def search_check(mode, home, output):
    """Exercise the actual configured search endpoint without copying credentials."""
    installed_home = Path((home / '.local/lib/codex-local/codex-home').read_text().strip())
    config_path = installed_home / ('config.toml' if mode == 'gui' else 'local.config.toml')
    config = tomllib.loads(config_path.read_text())
    base = config['model_providers'][config['model_provider']]['base_url'].rstrip('/')
    health_url = base.removesuffix('/v1') + '/healthz'
    with urllib.request.urlopen(health_url, timeout=20) as response:
        health = json.load(response)
    assert health.get('upstream') is True, 'Model upstream unavailable'
    assert health.get('search_available') is True, 'Search unavailable'
    assert health.get('tavily_configured') is False, 'This acceptance run must test keyless search'
    operations = {
        'search_query': [{'q': 'Ubuntu Linux official website'}],
        'open': [{'ref_id': 'https://example.com/'}],
        'find': [{'ref_id': 'https://example.com/', 'pattern': 'Example Domain'}],
    }
    results = {}
    for operation, items in operations.items():
        request = urllib.request.Request(base + '/alpha/search',
            data=json.dumps({'commands': {operation: items}, 'max_output_tokens': 2500}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        text = result.get('output', '')
        (output / f'search-{operation}.txt').write_text(text)
        if operation == 'search_query':
            entries = text.partition('\n')[2]
            assert 'https://' in entries and 'ubuntu' in entries.lower(), 'Search returned no relevant public results'
        else:
            content = text.partition('\n\n')[2]
            assert 'example domain' in content.lower(), f'{operation} did not retrieve the known public text'
            assert 'no matches' not in content.lower(), 'Find returned no matching content'
        results[operation] = True
    return {'keyless': True, 'backend': health.get('search_backend'), **results}


def model_tool_check(command, workspace, output, env):
    token = 'ACCEPTANCE_' + uuid.uuid4().hex
    marker = workspace / 'model-tool-proof.txt'
    prompt = (f'Use your shell tool to create model-tool-proof.txt in the current directory '
              f'with exactly this text: {token}. Read the file back using your shell tool. '
              f'Then reply with exactly {token}. Do not ask for confirmation.')
    result = subprocess.run([*command, 'exec', '--skip-git-repo-check', '--json',
                             '-c', 'approval_policy="never"', '-s', 'workspace-write',
                             '-C', str(workspace), prompt], env=env, text=True,
                            capture_output=True, timeout=600)
    (output / 'model-events.jsonl').write_text(result.stdout)
    (output / 'model-stderr.log').write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Model/tool execution exited {result.returncode}; see model-stderr.log')
    if not marker.is_file() or marker.read_text().strip() != token:
        raise RuntimeError('Model did not write the requested file through its tools')
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    messages = [event.get('item', {}) for event in events if event.get('type') == 'item.completed']
    if not any(item.get('type') == 'agent_message' and token in item.get('text', '') for item in messages):
        raise RuntimeError('Model did not return the requested completion token')
    return {'file_written': True, 'completion_token_received': True}


class Rpc:
    def __init__(self, binary, env, log):
        self.log = log.open('w')
        self.process = subprocess.Popen([str(binary), 'app-server'], env=env, text=True,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.messages = queue.Queue()
        self.seq = 0
        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    pass
            self.messages.put(None)
        threading.Thread(target=read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'codex_local_acceptance', 'version': '1'},
                                 'capabilities': {'experimentalApi': True}})
        self.process.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
        self.process.stdin.flush()

    def call(self, method, params):
        self.seq += 1
        self.process.stdin.write(json.dumps({'id': self.seq, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 60
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError('App server closed unexpectedly')
            if message.get('id') == self.seq:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"]}')
                return message['result']

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.log.close()


def project_check(binary, env, workspace, output):
    rpc = Rpc(binary, env, output / 'app-server.log')
    project_id = None
    try:
        project = rpc.call('project/create', {'name': 'VM acceptance ' + uuid.uuid4().hex,
                                             'roots': [], 'idempotencyKey': str(uuid.uuid4())})['project']
        project_id = project['id']
        assert not project['roots'], project
        roots = [{'path': str(workspace)}]
        project = rpc.call('project/update', {'projectId': project_id, 'roots': roots})['project']
        assert [root['path'] for root in project['roots']] == [str(workspace)], project
        project = rpc.call('project/read', {'projectId': project_id})['project']
        assert [root['path'] for root in project['roots']] == [str(workspace)], project
        return {'empty_project_created': True, 'repository_attached': True, 'project_read_back': True}
    finally:
        try:
            if project_id:
                rpc.call('project/delete', {'projectId': project_id})
        finally:
            rpc.close()


def gui_check(launcher, output, env):
    import websocket  # Ubuntu test prerequisite: python3-websocket
    if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
        raise RuntimeError('GUI acceptance requires a logged-in desktop display')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with (output / 'gui.log').open('w') as log:
        process = subprocess.Popen([str(launcher), f'--remote-debugging-port={port}',
                                    '--remote-debugging-address=127.0.0.1'],
                                   env=env, stdout=log, stderr=log)
        connection = None
        try:
            deadline = time.monotonic() + 90
            page = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'GUI exited early: {process.returncode}')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2) as response:
                        pages = json.load(response)
                    page = next((item for item in pages if item.get('type') == 'page'), None)
                    if page:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            if not page:
                raise RuntimeError('GUI renderer did not expose a page within 90 seconds')
            connection = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=20, suppress_origin=True)
            seq = 0
            def call(method, params=None):
                nonlocal seq
                seq += 1
                connection.send(json.dumps({'id': seq, 'method': method, 'params': params or {}}))
                while True:
                    message = json.loads(connection.recv())
                    if message.get('id') == seq:
                        if 'error' in message:
                            raise RuntimeError(message['error'])
                        return message['result']

            def evaluate(expression):
                result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
                if 'exceptionDetails' in result:
                    raise RuntimeError(result['exceptionDetails'].get('text', 'GUI JavaScript failed'))
                return result['result'].get('value')

            def wait_for(expression, timeout=30):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if evaluate(expression):
                        return
                    time.sleep(1)
                raise RuntimeError('GUI readiness timed out: ' + expression)

            call('Page.bringToFront')
            # A nested-virtualization desktop may take longer to finish its first mount.
            wait_for('(document.body?.innerText.trim().length ?? 0) > 30', timeout=180)
            text = evaluate('document.body.innerText') or ''
            if ('sign in' in text.lower() or 'log in' in text.lower()) and 'sign up' in text.lower():
                raise RuntimeError('GUI is showing authentication onboarding')
            if 'What type of work do you do?' in text:
                evaluate('Array.from(document.querySelectorAll("button")).find(b => b.innerText.trim() === "Skip").click()')
            wait_for("""!!document.querySelector('button[aria-label="Add new project"]')""", timeout=60)
            text = evaluate('document.body.innerText') or ''
            if 'Self-hosted llama.cpp' not in text:
                raise RuntimeError('GUI did not select its self-hosted provider')
            project_name = 'VM GUI acceptance ' + uuid.uuid4().hex[:8]
            evaluate("""document.querySelector('button[aria-label="Add new project"]').click()""")
            wait_for('Array.from(document.querySelectorAll("button[role=radio]")).some(b => b.innerText.startsWith("Local"))')
            evaluate('Array.from(document.querySelectorAll("button[role=radio]")).find(b => b.innerText.startsWith("Local")).click()')
            evaluate('Array.from(document.querySelectorAll("button")).find(b => b.innerText.trim() === "Next").click()')
            wait_for("""!!document.querySelector('input[placeholder="Project name"]')""")
            evaluate("""(()=>{const x=document.querySelector('input[placeholder="Project name"]');"""
                     'Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(x,'
                     + json.dumps(project_name) + ');x.dispatchEvent(new Event("input",{bubbles:true}))})()')
            evaluate('Array.from(document.querySelectorAll("button")).find(b => b.innerText.trim() === "Create project").click()')
            wait_for("""!document.querySelector('input[placeholder="Project name"]') && document.body.innerText.includes("""
                     + json.dumps(project_name) + ')')
            text = evaluate('document.body.innerText') or ''
            (output / 'gui-visible-text.txt').write_text(text)
            screenshot = call('Page.captureScreenshot', {'format': 'png'})
            (output / 'gui.png').write_bytes(base64.b64decode(screenshot['data']))
            return {'renderer_loaded': True, 'url': page.get('url'), 'visible_text_length': len(text),
                    'self_hosted_provider_visible': True, 'ui_project_created': project_name}
        finally:
            if connection:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['cli', 'gui'])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if 'microsoft' in os.uname().release.lower():
        parser.error('Run acceptance inside the disposable Ubuntu VM, not the WSL host')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    home = Path.home()
    runtime = home / '.local/lib/codex-local'
    env = dict(os.environ)
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CODEX_OSS_BASE_URL', 'CODEX_OSS_PORT', 'CODEX_CLI_PATH'):
        env.pop(key, None)
    report = {'mode': args.mode, 'kernel': os.uname().release, 'checks': {}, 'passed': False}
    try:
        report['checks']['search'] = search_check(args.mode, home, args.output_dir)
        with tempfile.TemporaryDirectory(prefix='codex-local-acceptance-') as directory:
            workspace = Path(directory)
            if args.mode == 'cli':
                assert not (home / '.local/bin/codex-local-gui').exists(), 'CLI installation installed GUI launcher'
                command = [str(home / '.local/bin/codex-local')]
            else:
                installed_home = Path((runtime / 'codex-home').read_text().strip())
                config = tomllib.loads((installed_home / 'config.toml').read_text())
                env['CODEX_HOME'] = str(installed_home)
                env['CODEX_SQLITE_HOME'] = config.get('sqlite_home', str(installed_home / 'sqlite'))
                binary = runtime / 'gui/backend/codex-local-gui-wsl'
                command = [str(binary)]
                report['checks']['projects'] = project_check(binary, env, workspace, args.output_dir)
                report['checks']['gui'] = gui_check(home / '.local/bin/codex-local-gui', args.output_dir, env)
            report['checks']['model_tools'] = model_tool_check(command, workspace, args.output_dir, env)
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (args.output_dir / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
