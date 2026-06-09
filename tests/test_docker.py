import pytest

from pocketlab import docker_api
from pocketlab.config import DockerConfig


class _FakeImage:
    def __init__(self, tags):
        self.tags = tags
        self.short_id = "sha256:abc"


class _FakeContainer:
    def __init__(self, name, status, tags):
        self.name = name
        self.status = status
        self.short_id = name[:12]
        self.image = _FakeImage(tags)
        self.actions = []

    def start(self): self.status = "running"; self.actions.append("start")
    def stop(self): self.status = "exited"; self.actions.append("stop")
    def restart(self): self.actions.append("restart")
    def reload(self): pass


class _FakeClient:
    def __init__(self, containers):
        self._containers = containers

        class _C:
            def list(inner, all=False):
                return containers

            def get(inner, cid):
                return next(c for c in containers if c.short_id == cid)

        self.containers = _C()


def test_list_containers_sorts_running_first(monkeypatch):
    fake = _FakeClient([
        _FakeContainer("zzz", "running", ["img:1"]),
        _FakeContainer("aaa", "exited", ["img:2"]),
        _FakeContainer("mmm", "running", []),
    ])
    monkeypatch.setattr(docker_api, "_client", lambda cfg: fake)
    out = docker_api.list_containers(DockerConfig())
    # running containers come first, then alphabetical
    assert [c["state"] for c in out] == ["running", "running", "exited"]
    assert out[-1]["name"] == "aaa"


def test_hide_filter(monkeypatch):
    fake = _FakeClient([
        _FakeContainer("keep", "running", ["i"]),
        _FakeContainer("secret-db", "running", ["i"]),
    ])
    monkeypatch.setattr(docker_api, "_client", lambda cfg: fake)
    out = docker_api.list_containers(DockerConfig(hide=["secret"]))
    assert [c["name"] for c in out] == ["keep"]


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        docker_api.container_action(DockerConfig(), "abc", "nuke")


def test_action_invokes_method(monkeypatch):
    c = _FakeContainer("web", "exited", ["i"])
    monkeypatch.setattr(docker_api, "_client", lambda cfg: _FakeClient([c]))
    res = docker_api.container_action(DockerConfig(), "web", "start")
    assert res["state"] == "running"
    assert "start" in c.actions
