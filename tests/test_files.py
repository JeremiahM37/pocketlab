import io

import pytest

from pocketlab import files
from pocketlab.config import FileRoot
from pocketlab.ssh import SSHError


def _root(tmp_path):
    return FileRoot(name="t", host="local", path=str(tmp_path))


def test_browse_lists_entries(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    listing = files.browse(_root(tmp_path), None)
    names = {e["name"]: e for e in listing.entries}
    assert names["a.txt"]["is_dir"] is False
    assert names["a.txt"]["size"] == 2
    assert names["sub"]["is_dir"] is True


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(files.FileError):
        files.browse(_root(tmp_path), "/etc")
    with pytest.raises(files.FileError):
        files.browse(_root(tmp_path), str(tmp_path) + "/../../etc")


def test_symlink_escape_rejected(tmp_path):
    # A symlink inside the root pointing outside must not be browsable.
    outside = tmp_path.parent / "outside_secret"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    link.symlink_to(outside)
    with pytest.raises(files.FileError):
        files.browse(_root(tmp_path), str(link))


def test_download_resolve_and_size_cap(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    resolved, local, size = files.resolve_download(_root(tmp_path), "big.bin", 1000)
    assert local is True and size == 100
    with pytest.raises(files.FileError):
        files.resolve_download(_root(tmp_path), "big.bin", 50)  # over cap


def test_upload_writes_file_and_rejects_path_in_name(tmp_path):
    res = files.save_upload(_root(tmp_path), None, "hello.txt", io.BytesIO(b"data"))
    assert res["size"] == 4
    assert (tmp_path / "hello.txt").read_bytes() == b"data"
    # filename with path components is reduced to its basename, never escapes
    files.save_upload(_root(tmp_path), None, "../evil.txt", io.BytesIO(b"x"))
    assert (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_upload_streams_in_chunks(tmp_path):
    # A payload larger than CHUNK_SIZE round-trips intact via chunked writes.
    payload = b"ab" * (files.CHUNK_SIZE + 1024)
    res = files.save_upload(_root(tmp_path), None, "big.bin", io.BytesIO(payload))
    assert res["size"] == len(payload)
    assert (tmp_path / "big.bin").read_bytes() == payload


def test_stream_process_yields_fixed_chunks(tmp_path):
    # stream_remote's engine: output arrives in fixed-size chunks, not one blob.
    f = tmp_path / "data.bin"
    payload = b"x" * (5 * 1024 + 100)
    f.write_bytes(payload)
    chunks = list(files._stream_process(["cat", str(f)], label="cat", chunk_size=1024))
    assert b"".join(chunks) == payload
    assert len(chunks) >= 6  # not buffered into a single blob
    assert all(len(c) <= 1024 for c in chunks)


def test_stream_process_surfaces_nonzero_exit():
    with pytest.raises(SSHError, match="boom"):
        list(files._stream_process(["sh", "-c", "echo hi; echo boom >&2; exit 3"], label="ssh x"))


def test_stream_process_reaps_on_early_close(tmp_path):
    # Consumer disconnecting mid-stream must not leak the child process.
    f = tmp_path / "data.bin"
    f.write_bytes(b"y" * (64 * 1024))
    gen = files._stream_process(["cat", str(f)], label="cat", chunk_size=1024)
    assert next(gen)  # start streaming
    gen.close()  # no leaked process; close() would raise if cleanup failed
