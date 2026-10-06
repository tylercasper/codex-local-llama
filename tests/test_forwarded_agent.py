import asyncio
from pathlib import Path
import stat
import sys

import pytest

HELPER = Path(__file__).parents[1] / 'scripts/validation/forwarded-agent.py'


@pytest.mark.asyncio
async def test_forwarded_agent_bridges_bytes_and_cleans_owned_socket(tmp_path):
    directory = tmp_path / 'ssh'
    directory.mkdir(mode=0o700)
    upstream = directory / 'forwarded.sock'
    destination = directory / 'model-agent.sock'
    source_file = directory / 'forwarded-agent'
    source_file.write_text(str(upstream) + '\n')
    async def echo(reader, writer):
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(echo, path=str(upstream))
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(HELPER), '--source-file', str(source_file), '--socket', str(destination),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        for _ in range(100):
            if destination.exists():
                break
            await asyncio.sleep(0.02)
        assert destination.exists()
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        reader, writer = await asyncio.open_unix_connection(str(destination))
        payload = b'\x00\x00\x00\x05\x0btest'
        writer.write(payload)
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(payload)), 3) == payload
        writer.close()
        await writer.wait_closed()
    finally:
        process.terminate()
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        server.close()
        await server.wait_closed()
    assert process.returncode == 0, stderr.decode()
    assert not stdout and not stderr
    assert not destination.exists()
    assert source_file.exists()
