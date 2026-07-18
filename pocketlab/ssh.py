"""Thin wrapper around the system OpenSSH client.

pocketlab talks to remote hosts via the ``ssh`` binary rather than a Python SSH
library. That means it transparently honours the operator's ``~/.ssh/config``,
agent, keys and known_hosts — the same connection the operator already uses
from a shell — with zero extra Python dependencies. Password auth is disabled
(``BatchMode=yes``): set up key-based auth, which is what you want anyway.
"""

from __future__ import annotations

import subprocess

# Seconds ssh waits to establish the TCP connection before giving up.
CONNECT_TIMEOUT = 5
# Overall wall-clock deadline for a remote command (connection + execution).
DEFAULT_COMMAND_TIMEOUT = 20.0

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
    "-o", "StrictHostKeyChecking=accept-new",
]


class SSHError(RuntimeError):
    pass


def run(
    target: str,
    command: str,
    *,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    stdin: bytes | None = None,
) -> bytes:
    """Run ``command`` on ``target`` (user@host) via ssh, returning stdout bytes.

    Raises SSHError on non-zero exit or timeout.
    """
    argv = ["ssh", *SSH_OPTS, target, command]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"ssh {target}: timed out after {timeout}s") from exc
    except FileNotFoundError as exc:  # ssh binary missing
        raise SSHError("ssh client not found on PATH") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
        raise SSHError(f"ssh {target}: {err}")
    return proc.stdout


def run_text(target: str, command: str, *, timeout: float = DEFAULT_COMMAND_TIMEOUT) -> str:
    return run(target, command, timeout=timeout).decode("utf-8", "replace")
