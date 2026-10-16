"""Serve the web app, the media directories and the API without nginx.

The container image puts nginx in front of Frigate. nginx terminates the
browser connection on port 5000/8971, serves the built web app and the media
directories, proxies the websocket servers on 5002 and go2rtc on 1984, and runs
an ``auth_request`` subrequest to ``/auth`` before handing a request to the API,
injecting the ``Remote-User`` and ``Remote-Role`` headers the API trusts.

A native Windows install has no nginx, so this module rebuilds that facade in
front of the same FastAPI app:

- ``/api/...`` is the API, with the auth result injected the same way nginx
  would inject it
- ``/vod/...`` is HLS for recorded video, built by :mod:`frigate.api.vod_native`
  instead of nginx-vod-module
- ``/clips``, ``/recordings`` and ``/exports`` are the media directories
- ``/ws``, ``/live/...`` and ``/go2rtc/...`` are proxied to the websocket
  servers inside Frigate and to go2rtc
- everything else is the built web app, with the ``try_files`` fallback nginx
  uses so client side routes work

Two details are worth calling out because they are easy to get wrong:

- the auth check calls :func:`frigate.api.auth.auth` in process rather than
  over HTTP. A loopback HTTP request would have to re-enter the server that is
  handling it, which deadlocks a single threaded server and wastes a socket
  hint in any other configuration.
- the check is an ASGI call into the API app, so it sees the same middleware
  stack, the same ``app`` state and the same database as a real request.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from frigate.api import vod_native
from frigate.api.auth import auth as auth_check
from frigate.const import CACHE_DIR, CLIPS_DIR, EXPORT_DIR, RECORD_DIR
from frigate.models import Event
from frigate.util.platform import (
    get_web_dir,
    get_web_host,
    get_web_port,
    web_dir_has_build,
)

logger = logging.getLogger(__name__)

# upstream services nginx proxies to
MQTT_WS_URL = "http://127.0.0.1:5002"
JSMEPEG_URL = "http://127.0.0.1:8082"
GO2RTC_URL = "http://127.0.0.1:1984"

# prefixes nginx runs auth_request for. Everything else, in particular the web
# app shell, is public. The go2rtc proxy is included even though nginx has no
# equivalent route: it forwards to go2rtc's own API, which reports the camera
# URLs (credentials included), so it must never answer anonymously.
PROTECTED_PREFIXES = (
    "/api/",
    "/vod/",
    "/clips/",
    "/recordings/",
    "/exports/",
    "/cache/",
    "/live/",
    "/go2rtc/",
)

PROTECTED_EXACT = ("/api", "/ws")

# nginx turns auth off for the login flow, which has to work before a session
# exists, and for the SPA fallbacks
AUTH_EXEMPT = (
    "/api/login",
    "/api/logout",
    "/api/auth/first_time_login",
)

PLAYERS = ("mse", "webrtc", "jsmpeg")

# headers a client may not set, because Frigate uses them to carry its own
# authentication decision
FORWARDED_REQUEST_HEADERS = (
    b"authorization",
    b"cookie",
    b"x-csrf-token",
)


def needs_auth(path: str) -> bool:
    """Whether nginx would run the auth subrequest for a path."""
    # the login flow has to work before a session exists, so the exemptions
    # are checked first: every exempt path also starts with a protected
    # prefix, and checking the prefixes first would make them unreachable
    if path in AUTH_EXEMPT:
        return False

    if path in PROTECTED_EXACT or path.startswith(PROTECTED_EXACT):
        return True

    return path.startswith(PROTECTED_PREFIXES)


# ---------------------------------------------------------------------------
# in process auth
# ---------------------------------------------------------------------------


def build_auth_scope(scope: dict, port: int) -> dict:
    """Build the scope the local auth check runs against.

    Mirrors what nginx passes along: the original method and URL, the port the
    client connected to, the credentials and the CSRF marker. Client supplied
    headers are deliberately not forwarded, which is what keeps a spoofed
    ``Remote-Role`` header from reaching the backend.
    """
    headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() in FORWARDED_REQUEST_HEADERS
    ]

    scheme = scope.get("scheme", "http")
    host = ""

    for key, value in scope.get("headers", []):
        if key.lower() == b"host":
            host = value.decode("latin-1")
            break

    raw_path = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
    query = scope.get("query_string", b"")
    original_url = f"{scheme}://{host}{raw_path.decode('latin-1')}"

    if query:
        original_url = f"{original_url}?{query.decode('latin-1')}"

    headers.append((b"x-original-method", scope.get("method", "GET").encode()))
    headers.append((b"x-original-url", original_url.encode("latin-1")))
    headers.append((b"x-server-port", str(port).encode()))
    headers.append((b"x-csrf-token", b"1"))

    return {
        "type": "http",
        "http_version": "1.1",
        "method": scope.get("method", "GET"),
        "scheme": scheme,
        "path": "/auth",
        "raw_path": b"/auth",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": scope.get("client") or ("127.0.0.1", 0),
        "server": scope.get("server") or ("127.0.0.1", port),
    }


class InProcessAuth:
    """Runs the auth endpoint without an HTTP round trip."""

    def __init__(self, api_app: Any) -> None:
        self.api_app = api_app

    async def __call__(
        self, scope: dict, port: int
    ) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        auth_scope = build_auth_scope(scope, port)
        auth_scope["app"] = self.api_app

        started = False
        status = 500
        headers: list[tuple[bytes, bytes]] = []
        body = bytearray()

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            nonlocal started, status, headers

            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        try:
            # the auth endpoint is a sync function, so it is called through the
            # same starlette request machinery a real request would use
            response = await asyncio.to_thread(auth_check, Request(auth_scope))

            if isinstance(response, Response):
                return response.status_code, response.raw_headers, response.body
        except Exception as err:  # pragma: no cover - defensive
            logger.exception("Authentication check failed: %s", err)

        if started:  # pragma: no cover - the auth endpoint returns a response
            return status, headers, bytes(body)

        return 500, [], b""


class NativeAuthMiddleware:
    """Inject ``Remote-User`` and ``Remote-Role`` like nginx's auth_request.

    A pure ASGI middleware rather than a ``BaseHTTPMiddleware`` because
    websockets need the same treatment: ``BaseHTTPMiddleware`` passes websocket
    scopes through untouched, which would leave ``/ws`` and the live view
    unauthenticated.
    """

    def __init__(self, app: Any, api_app: Any, port: int) -> None:
        self.app = app
        self.port = port
        self.auth = InProcessAuth(api_app)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        if not needs_auth(path):
            scope = dict(scope)
            scope["headers"] = self._strip_proxy_headers(scope)
            await self.app(scope, receive, send)
            return

        status, headers, payload = await self.auth(scope, self.port)

        if status >= 300:
            await self._deny(scope, status, headers, payload, receive, send)
            return

        scope = dict(scope)
        scope["headers"] = self._injected(headers) + self._strip_proxy_headers(scope)

        await self.app(scope, receive, send)

    @staticmethod
    def _injected(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        """The headers nginx forwards from the auth response to the backend."""
        wanted = (
            b"remote-user",
            b"remote-role",
            b"remote-groups",
            b"remote-email",
            b"remote-name",
        )

        return [(key, value) for key, value in headers if key.lower() in wanted]

    @staticmethod
    def _strip_proxy_headers(scope: dict) -> list[tuple[bytes, bytes]]:
        """Drop client supplied proxy headers so they are never trusted."""
        return [
            (key, value)
            for key, value in scope.get("headers", [])
            if not key.lower().startswith(b"remote-")
        ]

    async def _deny(
        self,
        scope: dict,
        status: int,
        headers: list[tuple[bytes, bytes]],
        payload: bytes,
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] == "websocket":
            # a websocket handshake cannot carry a status code, and the
            # websocket clients expect to reconnect rather than to be told why
            await send({"type": "websocket.close", "code": 1008})
            return

        response = Response(payload, status_code=status)

        for key, value in headers:
            if key.lower() in (b"location", b"set-cookie", b"content-type"):
                response.raw_headers.append((key, value))

        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# static files
# ---------------------------------------------------------------------------


def is_contained_in(path: str, directory: str) -> bool:
    """Containment check that survives symlinks pointing outside a root."""
    resolved = os.path.realpath(path)
    root = os.path.realpath(directory)

    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        # different Windows drives, so the paths are not comparable
        return False


class SpaFiles:
    """Static web app files with the nginx ``try_files`` fallback.

    ``try_files $uri $uri.html $uri/ /index.html`` is what makes client side
    routes such as ``/review`` work, so the same lookup order is reproduced.
    """

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.static = StaticFiles(directory=directory, html=True, check_dir=False)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.static(scope, receive, send)
            return

        path = scope.get("path", "/")
        candidate = self._candidate(path)

        if candidate is not None:
            response = FileResponse(candidate)

            # hashed asset names are safe to cache forever, the shell is not
            if path.startswith("/assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000"
            else:
                response.headers["Cache-Control"] = "no-store"

            await response(scope, receive, send)
            return

        index = os.path.join(self.directory, "index.html")

        if not os.path.isfile(index):
            await PlainTextResponse(
                "The Frigate web app is not built. Run 'npm install' and "
                "'npm run build' in the web directory to create it.",
                status_code=503,
            )(scope, receive, send)
            return

        await FileResponse(index, headers={"Cache-Control": "no-store"})(
            scope, receive, send
        )

    def _candidate(self, path: str) -> str | None:
        """Resolve a request path to a file the way ``try_files`` does."""
        relative = path.replace("\\", "/").lstrip("/")

        if not relative or ".." in relative.split("/"):
            return None

        direct = os.path.join(self.directory, relative)

        for candidate in (direct, f"{direct}.html"):
            if os.path.isfile(candidate) and is_contained_in(candidate, self.directory):
                return candidate

        if os.path.isdir(direct) and is_contained_in(direct, self.directory):
            index = os.path.join(direct, "index.html")

            if os.path.isfile(index):
                return index

        return None


# ---------------------------------------------------------------------------
# vod
# ---------------------------------------------------------------------------


def _playlist_or_error(
    camera: str, start: float, end: float, stream: str | None
) -> vod_native.VodPlaylist | Response:
    """Build a playlist, or the response explaining why it cannot be built."""
    if end <= start:
        return vod_native.vod_unavailable_response(
            "the end of the range must be after its start"
        )

    try:
        return vod_native.build_playlist(camera, start, end, _stream_preference(stream))
    except vod_native.VodUnavailable as err:
        return vod_native.vod_unavailable_response(str(err))


def _stream_preference(stream: str | None) -> str | None:
    """Only main and sub name a recorded stream."""
    if stream in ("main", "sub"):
        return stream

    return None


def _media_playlist(
    camera: str, start: float, end: float, stream: str | None
) -> Response:
    playlist = _playlist_or_error(camera, start, end, stream)

    if isinstance(playlist, Response):
        return playlist

    return vod_native.playlist_response(playlist)


def _master_playlist(
    camera: str, start: float, end: float, stream: str | None, base: str
) -> Response:
    playlist = _playlist_or_error(camera, start, end, stream)

    if isinstance(playlist, Response):
        return playlist

    return PlainTextResponse(
        playlist.master_m3u8(base),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


def _clone_base(camera: str, start: float, end: float, stream: str | None) -> str:
    """The URL prefix the master playlist points its variant at."""
    numbers = f"start/{start:g}/end/{end:g}"

    if stream:
        return f"/vod/{camera}/{stream}/{numbers}"

    return f"/vod/{camera}/{numbers}"


async def vod_playlist(request: Request) -> Response:
    """``/vod/{camera}[/{stream}]/start/{start}/end/{end}/{index,master}.m3u8``.

    The player asks for both names: ``master.m3u8`` when it wants a variant
    playlist to select from, ``index.m3u8`` when it wants the media playlist
    directly.
    """
    camera = request.path_params["camera"]
    start = float(request.path_params["start"])
    end = float(request.path_params["end"])
    stream = request.path_params.get("stream")
    name = request.path_params["playlist"]

    if name == "master.m3u8":
        return _master_playlist(
            camera, start, end, stream, _clone_base(camera, start, end, stream)
        )

    return _media_playlist(camera, start, end, stream)


async def vod_clip(request: Request) -> Response:
    """``/vod/clip/{camera}/start/{start}/end/{end}/{index,master}.m3u8``.

    The tracked object player uses this shape. The container answers it with
    HLS discontinuities enabled; a native playlist has real segment boundaries
    and does not need them.
    """
    camera = request.path_params["camera"]
    start = float(request.path_params["start"])
    end = float(request.path_params["end"])
    name = request.path_params["playlist"]

    if name == "master.m3u8":
        base = f"/vod/clip/{camera}/start/{start:g}/end/{end:g}"
        return _master_playlist(camera, start, end, None, base)

    return _media_playlist(camera, start, end, None)


async def vod_event(request: Request) -> Response:
    """``/vod/event/{event_id}/start/{start}/end/{end}/index.m3u8``."""
    event_id = request.path_params["event_id"]
    start = float(request.path_params["start"])
    end = float(request.path_params["end"])

    try:
        event = await asyncio.to_thread(Event.get, Event.id == event_id)
    except Event.DoesNotExist:
        return Response(status_code=404)

    playlist = _playlist_or_error(event.camera, start, end, None)

    if isinstance(playlist, Response):
        return playlist

    return vod_native.playlist_response(playlist)


async def vod_hour(request: Request) -> Response:
    """``/vod/{year_month}/{day}/{hour}/{camera}[/{tz}]/index.m3u8``."""
    camera = request.path_params["camera"]
    time_range = vod_native.hour_range(
        request.path_params["year_month"],
        int(request.path_params["day"]),
        int(request.path_params["hour"]),
        request.path_params.get("tz"),
    )

    if time_range is None:
        return vod_native.vod_unavailable_response("invalid date")

    return _media_playlist(camera, time_range[0], time_range[1], None)


async def vod_segment(request: Request) -> Response:
    """``/vod/{camera}[/{stream}]/start/{start}/end/{end}/{segment}.ts``."""
    camera = request.path_params["camera"]
    start = float(request.path_params["start"])
    end = float(request.path_params["end"])
    stream = request.path_params.get("stream")
    name = request.path_params["segment"]

    playlist = _playlist_or_error(camera, start, end, stream)

    if isinstance(playlist, Response):
        return playlist

    segment = vod_native.find_segment(playlist, name)

    if segment is None:
        return Response(status_code=404)

    path = await vod_native.render_segment(playlist, segment)

    if path is None:
        return Response(status_code=500)

    return vod_native.segment_response(path)


def vod_routes() -> list[Route]:
    """The vod URLs the web player asks for, in nginx-vod's shape."""
    return [
        Route(
            "/clip/{camera}/start/{start:float}/end/{end:float}/{playlist}.m3u8",
            vod_clip,
        ),
        Route(
            "/event/{event_id}/start/{start:float}/end/{end:float}/{playlist}.m3u8",
            vod_event,
        ),
        Route(
            "/{year_month}/{day:int}/{hour:int}/{camera}/{tz}/{playlist}.m3u8",
            vod_hour,
        ),
        Route(
            "/{year_month}/{day:int}/{hour:int}/{camera}/{playlist}.m3u8",
            vod_hour,
        ),
        Route(
            "/{camera}/stream/{stream}/start/{start:float}/end/{end:float}/{segment}.ts",
            vod_segment,
        ),
        Route(
            "/{camera}/start/{start:float}/end/{end:float}/{segment}.ts",
            vod_segment,
        ),
        Route(
            "/{camera}/{stream}/start/{start:float}/end/{end:float}/{playlist}.m3u8",
            vod_playlist,
        ),
        Route(
            "/{camera}/start/{start:float}/end/{end:float}/{playlist}.m3u8",
            vod_playlist,
        ),
    ]


# ---------------------------------------------------------------------------
# proxies
# ---------------------------------------------------------------------------


def upstream_for(path: str) -> tuple[str, str] | None:
    """Map a request path onto the upstream service nginx would use."""
    if path == "/ws":
        return MQTT_WS_URL, "/"

    if path.startswith("/live/jsmpeg/"):
        return GO2RTC_URL, path[len("/live/jsmpeg") :]

    if path.startswith("/live/mse/"):
        return GO2RTC_URL, path[len("/live/mse") :]

    if path.startswith("/live/webrtc/"):
        remainder = path[len("/live/webrtc") :]

        if remainder.startswith("/api/") or remainder == "/webrtc.html":
            return GO2RTC_URL, remainder

        return GO2RTC_URL, f"/{PLAYERS[1]}{remainder}"

    if path.startswith("/go2rtc/"):
        return GO2RTC_URL, path[len("/go2rtc") :]

    if path == "/api/go2rtc/webrtc":
        return GO2RTC_URL, "/api/webrtc"

    return None


async def websocket_endpoint(scope: dict, receive: Any, send: Any) -> None:
    """Bridge a browser websocket to the upstream websocket server."""
    upstream = upstream_for(scope.get("path", ""))

    if upstream is None:
        await send({"type": "websocket.close", "code": 1000})
        return

    url = f"{upstream[0]}{upstream[1]}"
    query = scope.get("query_string", b"").decode("latin-1")

    if query:
        url = f"{url}?{query}"

    accepted = False
    session = aiohttp.ClientSession()

    try:
        async with session.ws_connect(
            url.replace("http://", "ws://"),
            headers=_forwarded_headers(scope, websocket=True),
            heartbeat=None,
            max_msg_size=0,
        ) as upstream_ws:
            await send({"type": "websocket.accept"})
            accepted = True

            browser_to_upstream = asyncio.create_task(
                _pump_browser(receive, upstream_ws)
            )
            upstream_to_browser = asyncio.create_task(_pump_upstream(upstream_ws, send))

            done, pending = await asyncio.wait(
                (browser_to_upstream, upstream_to_browser),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
    except Exception as err:
        logger.debug("Websocket proxy to %s failed: %s", url, err)
    finally:
        await session.close()

        try:
            await send({"type": "websocket.close", "code": 1000 if accepted else 1011})
        except Exception:
            # the client may already be gone, which is not an error
            pass


async def _pump_browser(receive: Any, upstream_ws: Any) -> None:
    """Forward browser messages to the upstream server."""
    while True:
        message = await receive()

        if message["type"] == "websocket.disconnect":
            return

        if message["type"] != "websocket.receive":
            continue

        if message.get("text") is not None:
            await upstream_ws.send_str(message["text"])
        elif message.get("bytes") is not None:
            await upstream_ws.send_bytes(message["bytes"])


async def _pump_upstream(upstream_ws: Any, send: Any) -> None:
    """Forward upstream messages to the browser."""
    async for message in upstream_ws:
        if message.type == aiohttp.WSMsgType.TEXT:
            await send({"type": "websocket.send", "text": message.data})
        elif message.type == aiohttp.WSMsgType.BINARY:
            await send({"type": "websocket.send", "bytes": message.data})
        elif message.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            return


async def proxy_http(request: Request) -> Response:
    """Proxy a plain HTTP request upstream, which only go2rtc needs."""
    upstream = upstream_for(request.url.path)

    if upstream is None:
        return Response(status_code=404)

    url = f"{upstream[0]}{upstream[1]}"

    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = await request.body()
    session = aiohttp.ClientSession()

    try:
        async with session.request(
            request.method,
            url,
            data=body if body else None,
            headers=_forwarded_headers(request.scope),
        ) as upstream_response:
            content = await upstream_response.read()
            headers = {
                key: value
                for key, value in upstream_response.headers.items()
                if key.lower()
                not in (
                    "content-encoding",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                )
            }

            return Response(
                content,
                status_code=upstream_response.status_code,
                headers=headers,
                media_type=upstream_response.headers.get("Content-Type"),
            )
    except aiohttp.ClientError as err:
        logger.debug("Proxy to %s failed: %s", url, err)
        return Response(status_code=502)
    finally:
        await session.close()


def _forwarded_headers(scope: dict, websocket: bool = False) -> dict[str, str]:
    """Request headers to forward upstream."""
    skipped = {"host", "content-length"}

    if websocket:
        skipped |= {
            "connection",
            "upgrade",
            "sec-websocket-key",
            "sec-websocket-version",
            "sec-websocket-extensions",
        }

    return {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key.decode("latin-1").lower() not in skipped
    }


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------


async def _startup() -> None:
    """Start the background work the native server needs."""
    # stale vod cache directories are swept while the server runs
    vod_native.start_cache_maintenance()


@asynccontextmanager
async def _lifespan(app: Starlette) -> AsyncIterator[None]:
    """Run the startup work through the lifespan API.

    Starlette removed the ``on_startup`` and ``on_shutdown`` arguments, so the
    native server, which has no nginx in front of it, has to pass a lifespan
    context manager instead. Without this the app cannot be constructed at all
    and the Windows install never answers on any port.
    """
    await _startup()
    yield


def create_native_web_app(api_app: Any, port: int) -> Starlette:
    """Build the app that serves Frigate where nginx would.

    Args:
        api_app: the app from :func:`frigate.api.fastapi_app.create_fastapi_app`
        port: the port the server is bound to, which the auth check reports as
            the port the client connected to
    """
    web_dir = get_web_dir()

    if not web_dir_has_build():
        logger.warning(
            "No built web app found in %s. The web interface will not load "
            "until 'npm install' and 'npm run build' have been run in the web "
            "directory.",
            web_dir,
        )

    routes = [
        # the only API path nginx sends to go2rtc rather than to Frigate
        Route("/api/go2rtc/webrtc", proxy_http, methods=["POST", "OPTIONS"]),
        # the API, mounted where nginx proxies it
        Mount("/api", app=api_app),
        # recorded video, built here instead of by nginx-vod-module
        Mount("/vod", app=Starlette(routes=vod_routes())),
        # media directories
        Mount("/clips", app=StaticFiles(directory=CLIPS_DIR, check_dir=False)),
        Mount("/recordings", app=StaticFiles(directory=RECORD_DIR, check_dir=False)),
        Mount("/exports", app=StaticFiles(directory=EXPORT_DIR, check_dir=False)),
        Mount("/cache", app=StaticFiles(directory=CACHE_DIR, check_dir=False)),
        # live view and the websocket servers
        Route("/live/webrtc/webrtc.html", proxy_http, methods=["GET"]),
        WebSocketRoute("/ws", websocket_endpoint),
        WebSocketRoute("/live/jsmpeg/{path:path}", websocket_endpoint),
        WebSocketRoute("/live/mse/{path:path}", websocket_endpoint),
        WebSocketRoute("/live/webrtc/{path:path}", websocket_endpoint),
        Route("/go2rtc/{path:path}", proxy_http, methods=["GET", "POST", "OPTIONS"]),
        # the web app
        Mount("/", app=SpaFiles(web_dir)),
    ]

    app = Starlette(routes=routes, lifespan=_lifespan)
    app.add_middleware(NativeAuthMiddleware, api_app=api_app, port=port)
    return app


async def _watch_stop_event(servers: list, stop_event: Any) -> None:
    """Ask every listener to stop when Frigate's process event is set."""
    while not stop_event.is_set():
        await asyncio.sleep(0.1)

    for server in servers:
        server.should_exit = True


async def _serve(servers: list, stop_event: Any | None = None) -> None:
    """Run listeners until one exits or the process stop event is set."""
    server_tasks = [asyncio.create_task(server.serve()) for server in servers]
    stop_watcher = None

    if stop_event is not None:
        stop_watcher = asyncio.create_task(_watch_stop_event(servers, stop_event))

    try:
        wait_for = set(server_tasks)
        if stop_watcher is not None:
            wait_for.add(stop_watcher)

        # A Uvicorn listener exits on Ctrl+C, or when its startup fails. Other
        # listeners must be asked to stop too, otherwise gather waits forever.
        await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

        for server in servers:
            server.should_exit = True

        errors: list[BaseException] = []
        for task in server_tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    errors.append(error)

        await asyncio.gather(*server_tasks, return_exceptions=True)

        if errors:
            raise errors[0]
    finally:
        if stop_watcher is not None:
            stop_watcher.cancel()
            await asyncio.gather(stop_watcher, return_exceptions=True)

        for task in server_tasks:
            if not task.done():
                task.cancel()

        await asyncio.gather(*server_tasks, return_exceptions=True)


def run_native_web_server(
    api_app: Any, internal_port: int, stop_event: Any | None = None
) -> None:
    """Serve the web interface, the API and the media without nginx.

    Two things have to be reachable at once:

    - the web interface, on ``FRIGATE_WEB_HOST`` and the port from
      ``FRIGATE_WEB_PORT``, which defaults to the internal port
    - the internal port itself, on loopback, because Frigate's own consumers
      (export, motion search, debug replay) fetch vod playlists from it with
      ffmpeg and cannot authenticate

    When the two ports are the same, which is the default, one listener does
    both jobs and is treated as internal. Moving the web port to another port
    turns authentication on for that listener and leaves the internal port on
    loopback only.
    """
    import uvicorn

    host = get_web_host()
    port = get_web_port(internal_port)

    log_startup_banner(host, port, internal_port)

    public_server = uvicorn.Server(
        uvicorn.Config(
            create_native_web_app(api_app, port),
            host=host,
            port=port,
            log_level="error",
        )
    )

    servers = [public_server]

    if port != internal_port:
        internal_server = uvicorn.Server(
            uvicorn.Config(
                create_native_web_app(api_app, internal_port),
                host="127.0.0.1",
                port=internal_port,
                log_level="error",
                # the public listener already ran the application startup
                lifespan="off",
            )
        )
        # only one listener may install the process signal handlers
        internal_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        servers.append(internal_server)

    asyncio.run(_serve(servers, stop_event))


def log_startup_banner(host: str, port: int, internal_port: int) -> None:
    """Explain how the web interface is exposed."""
    logger.info("Serving the Frigate web interface on http://%s:%s", host, port)

    if port == internal_port:
        logger.info(
            "Port %s is Frigate's internal port, so requests to it are treated "
            "as internal: anyone who can reach it has admin access without "
            "logging in. Set FRIGATE_WEB_PORT to serve the interface with "
            "authentication instead.",
            port,
        )

    if host not in ("127.0.0.1", "localhost"):
        logger.info(
            "Binding to %s makes Frigate reachable from other devices on your "
            "network. Set FRIGATE_WEB_HOST=127.0.0.1 to restrict it to this "
            "computer.",
            host,
        )


__all__ = [
    "create_native_web_app",
    "log_startup_banner",
    "run_native_web_server",
]
