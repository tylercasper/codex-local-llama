#!/usr/bin/env python3
"""Bridge a forwarded SSH agent outside PrivateTmp for this disposable test VM.

The source file contains only the forwarded socket path, never a private key.
Keep the SSH forwarding session alive while this foreground helper runs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import signal
import stat


async def bridge(reader, writer, source_file):
    remote_writer = None
    tasks = []
    try:
        source = source_file.read_text().strip()
        if not source or '\n' in source or not Path(source).is_absolute():
            raise ValueError('Invalid forwarded agent socket path')
        remote_reader, remote_writer = await asyncio.open_unix_connection(source)
        async def transfer(incoming, outgoing):
            while data := await incoming.read(65536):
                outgoing.write(data)
                await outgoing.drain()
            if outgoing.can_write_eof():
                outgoing.write_eof()
        tasks = [asyncio.create_task(transfer(reader, remote_writer)),
                 asyncio.create_task(transfer(remote_reader, writer))]
        await asyncio.gather(*tasks)
    except (OSError, ValueError, asyncio.CancelledError):
        pass  # Agent/session unavailable: close client, never log agent traffic.
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for connection in (writer, remote_writer):
            if connection:
                connection.close()
                try:
                    await connection.wait_closed()
                except OSError:
                    pass


async def serve(source_file, destination):
    if not source_file.is_file():
        raise RuntimeError('Forwarded-agent source path file is missing')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = destination.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise RuntimeError('Agent socket directory must be owned by this user and not writable by others')
    # Never unlink an existing live or unrelated socket. Remove a stale socket
    # manually only after confirming its earlier proxy exited.
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('Agent destination already exists')
    previous_umask = os.umask(0o177)
    try:
        server = await asyncio.start_unix_server(
            lambda reader, writer: bridge(reader, writer, source_file), path=str(destination))
    finally:
        os.umask(previous_umask)
    socket_stat = destination.stat()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            loop.remove_signal_handler(signum)
        try:
            current = destination.lstat()
            if stat.S_ISSOCK(current.st_mode) and current.st_ino == socket_stat.st_ino:
                destination.unlink()
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', type=Path, default=Path.home() / '.ssh/forwarded-agent')
    parser.add_argument('--socket', type=Path, default=Path.home() / '.ssh/model-agent.sock')
    args = parser.parse_args()
    asyncio.run(serve(args.source_file, args.socket))


if __name__ == '__main__':
    main()
