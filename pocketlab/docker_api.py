"""Docker widget backend — list containers and run lifecycle actions.

Uses the official ``docker`` SDK (an optional dependency). The endpoint is a
no-op with a clear message if the SDK isn't installed or the daemon isn't
reachable, so the rest of the dashboard keeps working.

Remote Docker hosts are reached the standard Docker way: set ``docker.host`` to
``ssh://user@host`` or ``tcp://host:2375`` in the config — the SDK handles it.
"""

from __future__ import annotations

from typing import Any

from .config import DockerConfig

ALLOWED_ACTIONS = {"start", "stop", "restart"}


class DockerUnavailable(RuntimeError):
    """Raised when the Docker SDK or daemon can't be used."""


def _client(cfg: DockerConfig):
    try:
        import docker  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via DockerUnavailable
        raise DockerUnavailable(
            "Docker support not installed. Run: pip install 'pocketlab[docker]'"
        ) from exc

    base = cfg.host
    try:
        if base in ("local", "", None):
            return docker.from_env()
        return docker.DockerClient(base_url=base)
    except Exception as exc:  # docker.errors.DockerException et al.
        raise DockerUnavailable(f"cannot reach Docker daemon: {exc}") from exc


def _hidden(name: str, cfg: DockerConfig) -> bool:
    return any(pat and pat in name for pat in cfg.hide)


def list_containers(cfg: DockerConfig) -> list[dict[str, Any]]:
    client = _client(cfg)
    try:
        containers = client.containers.list(all=True)
    except Exception as exc:
        raise DockerUnavailable(f"failed to list containers: {exc}") from exc

    out: list[dict[str, Any]] = []
    for c in containers:
        name = c.name
        if _hidden(name, cfg):
            continue
        image = ""
        try:
            image = c.image.tags[0] if c.image.tags else (c.image.short_id or "")
        except Exception:
            pass
        out.append(
            {
                "id": c.short_id,
                "name": name,
                "state": c.status,  # running / exited / paused / created
                "image": image,
            }
        )
    out.sort(key=lambda x: (x["state"] != "running", x["name"].lower()))
    return out


def container_action(cfg: DockerConfig, container_id: str, action: str) -> dict[str, Any]:
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    client = _client(cfg)
    try:
        c = client.containers.get(container_id)
        getattr(c, action)()
        c.reload()
        return {"id": c.short_id, "name": c.name, "state": c.status}
    except DockerUnavailable:
        raise
    except Exception as exc:
        raise DockerUnavailable(f"{action} failed: {exc}") from exc
