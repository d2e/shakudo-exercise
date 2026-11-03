"""Entry point: `python -m api_service.main`. Settings are validated before serving (C6)."""
import logging
import sys

import uvicorn

from .app import RuntimeState, create_app
from .config import ConfigError, load_settings


class _Server(uvicorn.Server):
    """On SIGTERM readiness flips to 503 at once, before uvicorn drains in-flight requests (H7)."""

    def __init__(self, config: uvicorn.Config, state: RuntimeState):
        super().__init__(config)
        self._state = state

    def handle_exit(self, sig, frame):
        self._state.shutting_down = True
        super().handle_exit(sig, frame)


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")
    state = RuntimeState()
    app = create_app(settings, state=state)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_config=None,
                            timeout_graceful_shutdown=25)
    _Server(config, state).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
