import pytest
from pydantic import ValidationError

from pocketlab.config import Config, load_config


def test_default_config_is_usable():
    cfg = Config()
    assert cfg.title
    assert "system" in cfg.widgets
    assert cfg.hosts and cfg.hosts[0].stats == "local"


def test_missing_file_falls_back_to_default(tmp_path):
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert isinstance(cfg, Config)
    assert cfg.widgets  # default widgets


def test_load_from_yaml(tmp_path):
    p = tmp_path / "pocketlab.yaml"
    p.write_text(
        "title: Lab\n"
        "widgets: [system, docker, links]\n"
        "hosts:\n"
        "  - { name: a, stats: local }\n"
        "  - { name: b, stats: { ssh: root@host } }\n"
        "links:\n"
        "  - group: G\n"
        "    items:\n"
        "      - { name: X, url: 'http://x' }\n"
    )
    cfg = load_config(str(p))
    assert cfg.title == "Lab"
    assert cfg.host("b").ssh_target == "root@host"
    assert cfg.host("a").ssh_target is None
    assert cfg.links[0].items[0].icon  # default icon applied


def test_unknown_widget_rejected():
    with pytest.raises(ValidationError):
        Config(widgets=["system", "bogus"])


def test_public_config_hides_ssh_targets():
    cfg = Config(
        title="T",
        widgets=["system", "files"],
        hosts=[{"name": "a", "stats": {"ssh": "root@secret-host"}}],
        files={"roots": [{"name": "home", "host": "ssh:root@secret-host", "path": "/srv"}]},
    )
    pub = cfg.public()
    blob = str(pub)
    assert "secret-host" not in blob  # no ssh targets leak to the browser
    assert "/srv" not in blob  # no server-side paths leak
    assert pub["hosts"] == ["a"]
    assert pub["files"]["roots"] == ["home"]
