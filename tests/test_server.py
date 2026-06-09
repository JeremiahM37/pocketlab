import pytest
from fastapi.testclient import TestClient

from pocketlab.server import create_app


@pytest.fixture
def client(tmp_path):
    (tmp_path / "file.txt").write_text("hello")
    cfg = tmp_path / "pocketlab.yaml"
    cfg.write_text(
        "title: TestLab\n"
        "widgets: [system, docker, files, links]\n"
        "hosts:\n  - { name: local, stats: local }\n"
        f"files:\n  roots:\n    - {{ name: t, host: local, path: {tmp_path} }}\n"
        "links:\n  - group: G\n    items:\n      - { name: X, url: 'http://x' }\n"
    )
    return TestClient(create_app(str(cfg)))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_index_serves_shell(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "boot()" in r.text and 'id="panels"' in r.text


def test_manifest_and_sw(client):
    assert client.get("/manifest.json").status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in sw.headers["content-type"]


def test_config_endpoint(client):
    j = client.get("/api/config").json()
    assert j["title"] == "TestLab"
    assert j["widgets"] == ["system", "docker", "files", "links"]
    assert j["files"]["roots"] == ["t"]


def test_system_endpoint(client):
    j = client.get("/api/system").json()
    assert "hosts" in j and j["hosts"][0]["host"] == "local"


def test_links_endpoint(client):
    j = client.get("/api/links").json()
    assert j["groups"][0]["items"][0]["name"] == "X"


def test_files_browse(client):
    j = client.get("/api/files/browse", params={"root": "t"}).json()
    assert any(e["name"] == "file.txt" for e in j["entries"])


def test_files_unknown_root_404(client):
    assert client.get("/api/files/browse", params={"root": "nope"}).status_code == 404


def test_files_path_traversal_400(client):
    r = client.get("/api/files/browse", params={"root": "t", "path": "/etc"})
    assert r.status_code == 400


def test_docker_unknown_action_400(client):
    # action validation happens before any Docker contact
    r = client.post("/api/docker/containers/abc/nuke")
    assert r.status_code == 400


def test_disabled_widget_absent_from_config(client):
    # 'terminal' was not enabled in this config
    assert "terminal" not in client.get("/api/config").json()["widgets"]
