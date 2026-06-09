"""pocketlab configuration — load and validate ``pocketlab.yaml``.

The config is the single source of truth *and* the access-control boundary:
only hosts, docker endpoints, file roots and terminal ports listed here are
reachable. Anything not in the config returns 403/404. Keep the file tight.

A missing config file is fine — pocketlab falls back to a sensible default
(the local machine, system + links widgets) so ``pip install pocketlab &&
pocketlab`` works out of the box with zero setup.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# Widgets pocketlab knows how to render. The order in a user's `widgets:` list
# becomes the bottom-nav order; omitting one hides its tab entirely.
KNOWN_WIDGETS = ("system", "docker", "files", "terminal", "links")


class Host(BaseModel):
    """A machine to report system stats for."""

    name: str
    # "local" for this machine, or {"ssh": "user@host"} for a remote one.
    stats: Literal["local"] | dict[str, str] = "local"

    @property
    def ssh_target(self) -> str | None:
        if isinstance(self.stats, dict):
            return self.stats.get("ssh")
        return None


class DockerConfig(BaseModel):
    # "local" (unix socket), "ssh:user@host", or "tcp://host:2375".
    host: str = "local"
    # Hide containers whose name matches any of these (substring match).
    hide: list[str] = Field(default_factory=list)


class FileRoot(BaseModel):
    name: str
    host: str = "local"  # "local" or "ssh:user@host"
    path: str = "~"

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_null_path(cls, v: Any) -> Any:
        # YAML turns a bare `path: ~` into None; treat that as the home dir
        # rather than crashing, since "~" is the obvious intent.
        return "~" if v is None else v


class FilesConfig(BaseModel):
    roots: list[FileRoot] = Field(default_factory=list)
    # Hard cap on download size (MiB) to avoid streaming a 50GB file by accident.
    max_download_mib: int = 2048


class TerminalConfig(BaseModel):
    # "" => use the embedded mttyd mounted at /term-app. Otherwise an absolute
    # base URL of a separately-running mttyd ("https://term.example.com").
    mttyd_url: str = ""
    # ttyd ports to expose as terminal tiles. ttyd must already be listening on
    # them (pocketlab/mttyd do not spawn ttyd). See the README.
    ports: list[int] = Field(default_factory=lambda: [7681])
    # Optional friendly labels, parallel to `ports`.
    labels: list[str] = Field(default_factory=list)


class LinkItem(BaseModel):
    name: str
    url: str
    icon: str = "\U0001f517"  # 🔗
    desc: str = ""


class LinkGroup(BaseModel):
    group: str
    items: list[LinkItem] = Field(default_factory=list)


class Config(BaseModel):
    title: str = "pocketlab"
    widgets: list[str] = Field(default_factory=lambda: ["system", "links"])
    hosts: list[Host] = Field(default_factory=lambda: [Host(name="localhost", stats="local")])
    docker: DockerConfig = Field(default_factory=DockerConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    links: list[LinkGroup] = Field(default_factory=list)

    @field_validator("widgets")
    @classmethod
    def _known_widgets(cls, v: list[str]) -> list[str]:
        unknown = [w for w in v if w not in KNOWN_WIDGETS]
        if unknown:
            raise ValueError(
                f"unknown widget(s) {unknown}; valid widgets are {list(KNOWN_WIDGETS)}"
            )
        return v

    def host(self, name: str) -> Host | None:
        return next((h for h in self.hosts if h.name == name), None)

    def file_root(self, name: str) -> FileRoot | None:
        return next((r for r in self.files.roots if r.name == name), None)

    def public(self) -> dict[str, Any]:
        """Config safe to ship to the browser — no SSH targets, no secrets.

        The frontend only needs to know *which* widgets/tabs to render and the
        display names of hosts/roots/links. It never needs SSH credentials or
        connection strings, so those stay server-side.
        """
        return {
            "title": self.title,
            "widgets": [w for w in self.widgets if w in KNOWN_WIDGETS],
            "hosts": [h.name for h in self.hosts],
            "files": {"roots": [r.name for r in self.files.roots]},
            "terminal": {
                "mttyd_url": self.terminal.mttyd_url,
                "ports": self.terminal.ports,
                "labels": self.terminal.labels,
            },
            "links": [g.model_dump() for g in self.links],
        }


def load_config(path: str | None = None) -> Config:
    """Load config from ``path``, ``$POCKETLAB_CONFIG``, or ./pocketlab.yaml.

    Returns the built-in default Config if no file is found.
    """
    path = path or os.environ.get("POCKETLAB_CONFIG") or "pocketlab.yaml"
    if not os.path.exists(path):
        return Config()
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Config.model_validate(data)
