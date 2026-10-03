import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import webuntis_api_v2  # noqa: E402

app = webuntis_api_v2.app
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


def _options_path() -> Path:
    return Path("/data/options.json")


def _read_options() -> Dict[str, Any]:
    fallback = {
        "server": "demo.local",
        "school": "demo",
        "username": "Demo",
        "password": "Demo",
        "auto_login": False,
        "days": 7,
    }
    try:
        data = json.loads(_options_path().read_text(encoding="utf-8"))
        return {**fallback, **data}
    except Exception:
        return fallback


@app.get("/api/addon/options")
def addon_options() -> Dict[str, Any]:
    options = _read_options()
    return {
        "status": True,
        "data": {
            "server": options.get("server", ""),
            "school": options.get("school", ""),
            "username": options.get("username", ""),
            "password": options.get("password", ""),
            "auto_login": bool(options.get("auto_login", False)),
            "days": int(options.get("days", 7) or 7),
        },
        "message": "Add-on options loaded",
    }


@app.get("/app", response_class=FileResponse)
def web_app() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html")


def _patch_root() -> None:
    for route in app.routes:
        if getattr(route, "path", None) == "/" and "GET" in getattr(route, "methods", set()):
            route.endpoint = web_app
            route.name = "web_app"
            route.response_class = FileResponse


if __name__ == "__main__":
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8099"))
    _patch_root()
    webuntis_api_v2.BIND.update(host=host, hosts=[host], port=port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("LOG_LEVEL", "info").lower())
