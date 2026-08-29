"""Desktop / sidecar entry point: run the FastAPI gateway programmatically.

Used both by PyInstaller (frozen sidecar) and by `python -m app` during
development.  The Electron shell injects:

- ``CLOUD123_PORT``  — free port chosen by the shell (default 8000)
- ``DATA_DIR``       — per-user application data directory (default <repo>/data)
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from .main import app

    port = int(os.environ.get("CLOUD123_PORT") or "8000")
    host = os.environ.get("CLOUD123_HOST") or "127.0.0.1"
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
