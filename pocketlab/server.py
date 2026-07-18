"""pocketlab HTTP server — serves the PWA and a small self-contained JSON API.

    GET  /                              the installable PWA shell (app.html)
    GET  /manifest.json /sw.js          PWA plumbing (served at root scope)
    GET  /healthz                       liveness
    GET  /api/config                    public config: which widgets/tabs render
    GET  /api/system                    per-host stats (cpu/mem/disk/temp/uptime)
    GET  /api/links                     service-link groups
    GET  /api/docker/containers         container list
    POST /api/docker/containers/{id}/{action}   start|stop|restart
    GET  /api/files/browse?root&path    directory listing within a root
    GET  /api/files/download?root&path  download a file
    POST /api/files/upload?root&path    upload a file

When the `terminal` widget is enabled and no external `terminal.mttyd_url` is
set, an embedded mttyd app is mounted at /term-app and the Terminal tab iframes
/term-app/term/{port}.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import docker_api, files, system
from .config import Config, load_config
from .docker_api import ContainerNotFound, DockerUnavailable
from .files import FileError

HERE = Path(__file__).parent
STATIC = HERE / "static"

logger = logging.getLogger("pocketlab")


def create_app(config_path: str | None = None) -> FastAPI:
    cfg: Config = load_config(config_path)
    app = FastAPI(title="pocketlab", version="0.1.0")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # ── PWA shell + plumbing ─────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "app.html").read_text(encoding="utf-8"))

    @app.get("/manifest.json")
    async def manifest() -> Response:
        return Response(
            (STATIC / "manifest.json").read_text(encoding="utf-8"),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js")
    async def service_worker() -> Response:
        # Must be served at root scope so it can control the whole origin.
        return Response(
            (STATIC / "sw.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "widgets": cfg.public()["widgets"]}

    # ── config + widgets ─────────────────────────────────────────────────────
    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        return JSONResponse(cfg.public())

    # NOTE: handlers below are deliberately plain `def`, not `async def`. They
    # do blocking I/O (psutil sampling, ssh subprocesses with multi-second
    # timeouts, docker SDK calls, disk writes); as sync handlers FastAPI runs
    # them in its threadpool instead of freezing the event loop for everyone.
    @app.get("/api/system")
    def api_system() -> JSONResponse:
        return JSONResponse({"hosts": system.all_stats(cfg.hosts)})

    @app.get("/api/links")
    async def api_links() -> JSONResponse:
        return JSONResponse({"groups": [g.model_dump() for g in cfg.links]})

    # ── docker ───────────────────────────────────────────────────────────────
    @app.get("/api/docker/containers")
    def api_docker_list() -> JSONResponse:
        try:
            return JSONResponse({"containers": docker_api.list_containers(cfg.docker)})
        except DockerUnavailable as exc:
            return JSONResponse({"containers": [], "error": str(exc)}, status_code=503)

    @app.post("/api/docker/containers/{container_id}/{action}")
    def api_docker_action(container_id: str, action: str) -> JSONResponse:
        try:
            return JSONResponse(docker_api.container_action(cfg.docker, container_id, action))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ContainerNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except DockerUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    # ── files ────────────────────────────────────────────────────────────────
    def _root_or_404(name: str):
        root = cfg.file_root(name)
        if root is None:
            raise HTTPException(404, f"unknown file root {name!r}")
        return root

    def _upstream_error(op: str, root, exc: Exception) -> HTTPException:
        # ssh stderr / exception text can leak server paths, usernames and
        # host details — log it here, hand the client a generic message that
        # still names the root so the operator knows where to look.
        logger.error("files %s failed for root %r (host %s): %s", op, root.name, root.host, exc)
        return HTTPException(502, f"upstream error on file root {root.name!r} — see server log")

    @app.get("/api/files/browse")
    def api_files_browse(
        root: str = Query(...), path: str | None = Query(None)
    ) -> JSONResponse:
        r = _root_or_404(root)
        try:
            listing = files.browse(r, path)
        except FileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # ssh failures, etc.
            raise _upstream_error("browse", r, exc) from exc
        return JSONResponse({"path": listing.path, "entries": listing.entries})

    @app.get("/api/files/download")
    def api_files_download(root: str = Query(...), path: str = Query(...)):
        r = _root_or_404(root)
        max_bytes = cfg.files.max_download_mib * 1024 * 1024
        try:
            resolved, local, _size = files.resolve_download(r, path, max_bytes)
        except FileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise _upstream_error("download", r, exc) from exc
        name = os.path.basename(resolved)
        if local:
            return FileResponse(resolved, filename=name)
        return StreamingResponse(
            files.stream_remote(r, resolved),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/files/upload")
    def api_files_upload(
        root: str = Query(...),
        path: str | None = Query(None),
        file: UploadFile = File(...),
    ) -> JSONResponse:
        r = _root_or_404(root)
        try:
            # Hand save_upload the underlying (spooled) file object so the
            # upload is streamed to its destination in chunks, never slurped
            # into memory as one bytes blob.
            result = files.save_upload(r, path, file.filename or "upload", file.file)
        except FileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise _upstream_error("upload", r, exc) from exc
        return JSONResponse({"success": True, **result})

    # ── embedded terminal (mttyd) ────────────────────────────────────────────
    if "terminal" in cfg.widgets and not cfg.terminal.mttyd_url:
        try:
            from mttyd.server import create_app as mttyd_app

            app.mount("/term-app", mttyd_app(None))
        except ImportError:
            # mttyd not installed; the Terminal tab will show an install hint.
            pass

    return app


def main() -> None:
    p = argparse.ArgumentParser(prog="pocketlab")
    p.add_argument("--config", default=os.environ.get("POCKETLAB_CONFIG"),
                   help="Path to pocketlab.yaml (default: ./pocketlab.yaml)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8838)
    args = p.parse_args()

    import uvicorn

    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
