import pytest

from pocketlab import files
from pocketlab.config import FileRoot


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
    res = files.save_upload(_root(tmp_path), None, "hello.txt", b"data")
    assert res["size"] == 4
    assert (tmp_path / "hello.txt").read_bytes() == b"data"
    # filename with path components is reduced to its basename, never escapes
    files.save_upload(_root(tmp_path), None, "../evil.txt", b"x")
    assert (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()
