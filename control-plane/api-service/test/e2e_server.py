"""A real API on a real port for end-to-end tests (the CLI, a WebSocket client): uvicorn in a background thread.

    base_url, gateway, stop = run_api()
    ...  # talk HTTP and WebSocket to base_url; script the cluster through `gateway` (support.FakeGateway)
    stop()

`stop.bootstrap_secret` and `stop.bootstrap_secret_file` let a client log in through the mock identity,
`stop.settings` is the Settings in use. The cluster is the in-memory FakeGateway, so nothing touches Kubernetes.
"""
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import uvicorn

from api_service.app import create_app
from support import BOOTSTRAP, FakeGateway, make_settings


def run_api(settings_overrides: Optional[dict] = None, gateway: Optional[FakeGateway] = None
            ) -> tuple[str, FakeGateway, Callable[[], None]]:
    gateway = gateway if gateway is not None else FakeGateway()
    tmp = tempfile.TemporaryDirectory()
    secret_file = Path(tmp.name) / "bootstrap"
    secret_file.write_text(BOOTSTRAP)
    settings = make_settings(**{"bootstrap_secret_file": str(secret_file), **(settings_overrides or {})})
    app = create_app(settings, gateway=gateway)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off",
                            ws_ping_interval=20, ws_ping_timeout=20)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="e2e-api")
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the test API did not start")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)
        tmp.cleanup()

    stop.bootstrap_secret, stop.bootstrap_secret_file, stop.settings = BOOTSTRAP, str(secret_file), settings  # type: ignore[attr-defined]
    return f"http://127.0.0.1:{port}", gateway, stop
