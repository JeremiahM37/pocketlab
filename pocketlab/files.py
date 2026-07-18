"""File-browser backend — browse / download / upload within configured roots.

Each root pins a base directory on a host (local or ssh). All access is
*confined to that base*: a resolved path that escapes the root (via ``..``,
symlinks, or an absolute path elsewhere) is rejected. This is the access
boundary — a misbehaving or malicious client cannot read ``/etc/shadow`` unless
a root explicitly exposes it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

from .config import FileRoot
from .ssh import SSH_OPTS, SSHError, run_text

# Fixed chunk size for streaming transfers (download + upload) so large files
# never get buffered whole in memory.
CHUNK_SIZE = 256 * 1024


class FileError(Exception):
    """Bad request — invalid root, path escape, or missing file (HTTP 400)."""


@dataclass
class Listing:
    path: str
    entries: list[dict[str, Any]]


def _base(root: FileRoot) -> str:
    return os.path.normpath(os.path.expanduser(root.path))


def _confine(root: FileRoot, path: str | None) -> str:
    """Normalise ``path`` and ensure it stays inside the root's base dir."""
    base = _base(root)
    if not path:
        return base
    candidate = path if os.path.isabs(path) else os.path.join(base, path)
    candidate = os.path.normpath(candidate)
    # Lexical containment. (Local browse additionally realpath-checks below to
    # defeat symlink escapes; remote hosts are operator-trusted endpoints.)
    if candidate != base and not candidate.startswith(base + os.sep):
        raise FileError("path escapes the configured root")
    return candidate


def _ssh_target(root: FileRoot) -> str:
    # "ssh:user@host" -> "user@host"; bare "user@host" also accepted.
    return root.host.split("ssh:", 1)[1] if root.host.startswith("ssh:") else root.host


def is_local(root: FileRoot) -> bool:
    return root.host in ("local", "", None)


# ── browse ───────────────────────────────────────────────────────────────────

def browse(root: FileRoot, path: str | None) -> Listing:
    target = _confine(root, path)
    if is_local(root):
        return _browse_local(root, target)
    return _browse_ssh(root, target)


def _browse_local(root: FileRoot, target: str) -> Listing:
    real = os.path.realpath(target)
    base_real = os.path.realpath(_base(root))
    if real != base_real and not real.startswith(base_real + os.sep):
        raise FileError("path escapes the configured root")
    if not os.path.isdir(real):
        raise FileError("not a directory")
    entries = []
    with os.scandir(real) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                {
                    "name": e.name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "modified": int(st.st_mtime),
                }
            )
    return Listing(path=target, entries=entries)


def _browse_ssh(root: FileRoot, target: str) -> Listing:
    q = shlex.quote(target)
    # GNU find printf: type, size, mtime(epoch), name — one entry per line.
    cmd = f"find {q} -maxdepth 1 -mindepth 1 -printf '%y\\t%s\\t%T@\\t%f\\n'"
    out = run_text(_ssh_target(root), cmd, timeout=20.0)
    entries = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        ftype, size, mtime, name = parts
        entries.append(
            {
                "name": name,
                "is_dir": ftype == "d",
                "size": 0 if ftype == "d" else int(size or 0),
                "modified": int(float(mtime or 0)),
            }
        )
    return Listing(path=target, entries=entries)


# ── download ──────────────────────────────────────────────────────────────────

def resolve_download(root: FileRoot, path: str, max_bytes: int) -> tuple[str, bool, int]:
    """Validate a download request. Returns (resolved_path, is_local, size)."""
    target = _confine(root, path)
    if is_local(root):
        real = os.path.realpath(target)
        base_real = os.path.realpath(_base(root))
        if real != base_real and not real.startswith(base_real + os.sep):
            raise FileError("path escapes the configured root")
        if not os.path.isfile(real):
            raise FileError("not a file")
        size = os.path.getsize(real)
        if size > max_bytes:
            raise FileError(f"file too large ({size} bytes > {max_bytes} limit)")
        return real, True, size
    # remote: stat size first to enforce the cap before streaming
    q = shlex.quote(target)
    out = run_text(_ssh_target(root), f"stat -c '%s' {q}", timeout=15.0).strip()
    try:
        size = int(out)
    except ValueError as exc:
        raise FileError("not a file") from exc
    if size > max_bytes:
        raise FileError(f"file too large ({size} bytes > {max_bytes} limit)")
    return target, False, size


def _stream_process(argv: list[str], label: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Spawn ``argv`` and yield its stdout in fixed-size chunks.

    The process is always reaped; a non-zero exit raises SSHError (with stderr)
    after the output has been drained, and an early consumer disconnect kills
    the child instead of leaking it.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            chunk = proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk
        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            raise SSHError(f"{label}: {err or f'exit code {rc}'}")
    finally:
        if proc.poll() is None:  # consumer stopped early — don't leak the child
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def stream_remote(root: FileRoot, target: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Yield bytes of a remote file over ssh (used by a StreamingResponse).

    Streams ``cat`` output chunk-by-chunk rather than buffering the whole file
    (up to the multi-GiB download cap) in memory.
    """
    host = _ssh_target(root)
    argv = ["ssh", *SSH_OPTS, host, f"cat -- {shlex.quote(target)}"]
    yield from _stream_process(argv, label=f"ssh {host}", chunk_size=chunk_size)


# ── upload ────────────────────────────────────────────────────────────────────

def _pipe_to_process(argv: list[str], label: str, stream: BinaryIO,
                     timeout: float = 300.0) -> int:
    """Feed ``stream`` to ``argv``'s stdin in chunks; return total bytes piped."""
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    size = 0
    try:
        try:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                proc.stdin.write(chunk)
                size += len(chunk)
            proc.stdin.close()
        except BrokenPipeError:
            pass  # remote side died — surfaced via exit code below
        rc = proc.wait(timeout=timeout)
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            raise SSHError(f"{label}: {err or f'exit code {rc}'}")
    except subprocess.TimeoutExpired as exc:
        raise SSHError(f"{label}: timed out after {timeout}s") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stderr.close()
    return size


def save_upload(
    root: FileRoot, path: str | None, filename: str, stream: BinaryIO
) -> dict[str, Any]:
    """Write an uploaded file, reading ``stream`` in chunks (never whole-file)."""
    # Reject path components in the filename — uploads land directly in `path`.
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in (".", ".."):
        raise FileError("invalid filename")
    target_dir = _confine(root, path)
    dest = _confine(root, os.path.join(target_dir, safe_name))

    if is_local(root):
        real_dir = os.path.realpath(target_dir)
        base_real = os.path.realpath(_base(root))
        if real_dir != base_real and not real_dir.startswith(base_real + os.sep):
            raise FileError("path escapes the configured root")
        if not os.path.isdir(real_dir):
            raise FileError("upload directory does not exist")
        size = 0
        with open(os.path.join(real_dir, safe_name), "wb") as fh:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                fh.write(chunk)
                size += len(chunk)
    else:
        host = _ssh_target(root)
        argv = ["ssh", *SSH_OPTS, host, f"cat > {shlex.quote(dest)}"]
        size = _pipe_to_process(argv, label=f"ssh {host}", stream=stream)
    return {"name": safe_name, "size": size}
