"""Fan completed desktop-server REST write responses out to every open client."""

from __future__ import annotations

from typing import Callable, TypeVar

from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core import data_events

_COMPUTE_ONLY = "__onw_compute_only__"
_Endpoint = TypeVar("_Endpoint", bound=Callable)


def compute_only(endpoint: _Endpoint) -> _Endpoint:
    """Mark a POST route that only computes and writes nothing, so a call to it is not announced
    as a data change. Announcing them made every open screen refetch on each Outlook simulation
    (dragging the Retire pill posts one per move). Mark a route only after checking it never
    writes: the mark lives on the route, so a rename or a new route can't silently drift from it.
    """
    setattr(endpoint, _COMPUTE_ONLY, True)
    return endpoint


def _is_compute_only(scope: Scope) -> bool:
    # Resolved from the app's own routes rather than read back from scope["endpoint"] after the
    # call: a middleware that copies the scope (DemoVisitorMiddleware does) would hide that.
    router = getattr(scope.get("app"), "router", None)
    for route in getattr(router, "routes", ()):
        match, child = route.matches(scope)
        if match is Match.FULL:
            return bool(getattr(child.get("endpoint"), _COMPUTE_ONLY, False))
    return False


class DataChangeMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}
            or not scope["path"].startswith("/api/")
            or _is_compute_only(scope)
        ):
            await self.app(scope, receive, send)
            return
        status = 500
        published = False

        async def completed(message: Message) -> None:
            nonlocal status, published
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and 200 <= status < 300
                and not published
            ):
                published = True
                data_events.publish(["*"])

        await self.app(scope, receive, completed)
