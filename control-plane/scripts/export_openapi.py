"""Write the OpenAPI contract to control-plane/spec/openapi.yaml (F8). Run via `make openapi`.

Usage: python export_openapi.py [--check]   (--check exits 1 when the committed file is stale, F9)
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api-service" / "src"))

from api_service.app import create_app  # noqa: E402
from api_service.config import Settings  # noqa: E402

TARGET = ROOT / "spec" / "openapi.yaml"


def render() -> str:
    # A dev app with mock auth shows the full surface; the gateway is never called to build the schema.
    app = create_app(Settings(), gateway=object())
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if TARGET.exists() and TARGET.read_text() == text else 1)
    TARGET.write_text(text)
    print(f"wrote {TARGET} ({len(text.splitlines())} lines)")
