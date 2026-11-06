"""/.well-known/openapi.json serves the same document as /openapi.json.

Both routes call the existing builder. There is no redirect, so clients that
do not follow redirects still get the schema, and there is no redirect loop.
The well-known path stays out of the OpenAPI paths object. Discovery may
advertise it next to the canonical path.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

CANONICAL_PATH = "/openapi.json"
WELL_KNOWN_PATH = "/.well-known/openapi.json"


def manifest_endpoints() -> Dict[str, str]:
    """Additive discovery paths. The canonical path stays /openapi.json."""
    return {
        "openapi": CANONICAL_PATH,
        "openapiWellKnown": WELL_KNOWN_PATH,
    }


def register_well_known_openapi(app: Any, handler: Callable[..., Any]) -> None:
    """Attach the existing /openapi.json handler to the well-known path."""
    app.add_api_route(
        WELL_KNOWN_PATH,
        handler,
        methods=["GET"],
        include_in_schema=False,
        name="openapi_well_known",
    )
