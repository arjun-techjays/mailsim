"""Write the frozen OpenAPI document: `python -m mailsim.api.openapi [path]`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from mailsim.api.app import create_app


def document() -> dict[str, Any]:
    return create_app().openapi()


def render() -> str:
    return json.dumps(document(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else Path("openapi/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
