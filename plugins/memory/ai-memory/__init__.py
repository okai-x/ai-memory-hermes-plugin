from __future__ import annotations

import sys
from pathlib import Path

_init_dir = str(Path(__file__).resolve().parent)
if _init_dir not in sys.path:
    sys.path.insert(0, _init_dir)

# Hermes loads __init__.py as a standalone module (not a package), so
# relative imports (from .foo import Bar) fail. Bare imports + sys.path
# ensure sibling modules are found regardless of how the plugin is loaded.
from provider import AiMemoryProvider  # noqa: E402

__all__ = ["register"]


def register(ctx: object) -> None:
    # Registration can run in one process serving several profiles. Bind the
    # provider only when Hermes passes that profile's home to initialize().
    ctx.register_memory_provider(AiMemoryProvider())  # type: ignore[union-attr]
