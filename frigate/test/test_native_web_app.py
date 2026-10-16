"""The native web server, which replaces nginx on Windows."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.requests import Request
from starlette.responses import Response

from frigate.api.auth import deny_response_for_go2rtc_stream
from frigate.api.web import (
    InProcessAuth,
    NativeAuthMiddleware,
    _serve,
    build_auth_scope,
    create_native_web_app,
    needs_auth,
)


class _FakeServer:
    """Small Uvicorn-like listener for shutdown coordination tests."""

    def __init__(self, exits_immediately: bool = False) -> None:
        self.should_exit = False
        self.exits_immediately = exits_immediately
        self.started = asyncio.Event()

    async def serve(self) -> None:
        self.started.set()

        if self.exits_immediately:
            return

        while not self.should_exit:
            await asyncio.sleep(0.01)


class TestNativeWebServerShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_stop_event_stops_every_listener(self) -> None:
        """A service event must stop Uvicorn so Frigate can shut down cleanly."""
        stop_event = threading.Event()
        servers = [_FakeServer(), _FakeServer()]
        serving = asyncio.create_task(_serve(servers, stop_event))

        try:
            await asyncio.gather(*(server.started.wait() for server in servers))
            stop_event.set()
            await asyncio.wait_for(serving, timeout=2)
        finally:
            if not serving.done():
                stop_event.set()
                await asyncio.wait_for(serving, timeout=2)

        self.assertTrue(all(server.should_exit for server in servers))

    async def test_exit_of_one_listener_stops_the_other(self) -> None:
        """Ctrl+C reaches one Uvicorn server, not the loopback listener."""
        servers = [_FakeServer(exits_immediately=True), _FakeServer()]

        await asyncio.wait_for(_serve(servers), timeout=2)

        self.assertTrue(all(server.should_exit for server in servers))


class TestNativeWebApp(unittest.TestCase):
    def test_app_carries_the_startup_work_as_a_lifespan(self):
        """Starlette removed on_startup, so a lifespan has to be provided.

        Passing the removed argument raised TypeError while the config was
        loading, which meant the native install never answered on any port.
        """
        with patch("frigate.api.web.vod_native.start_cache_maintenance") as sweep:
            app = create_native_web_app(MagicMock(), 5000)

        # the startup work is reachable through the app rather than by calling
        # the helper directly, which is what the lifespan is for
        self.assertTrue(callable(app.router.lifespan_context))
        sweep.assert_not_called()

    def test_in_process_auth_blocks_unknown_go2rtc_streams(self):
        """Native auth retains the raw path needed by the stream security check."""
        for path in (
            "/live/mse/api/ws",
            "/live/webrtc/api/ws",
            "/api/go2rtc/webrtc",
        ):
            with self.subTest(path=path):
                scope = {
                    "type": "http",
                    "method": "GET",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"src=unknown_stream",
                    "headers": [(b"host", b"frigate.local")],
                }
                auth_scope = build_auth_scope(scope, 8971)
                auth_scope["app"] = SimpleNamespace(frigate_config=MagicMock())
                request = Request(auth_scope)
                original_url = dict(auth_scope["headers"])[b"x-original-url"].decode()

                with patch(
                    "frigate.api.auth._is_existing_go2rtc_stream",
                    return_value=False,
                ):
                    self.assertEqual(
                        deny_response_for_go2rtc_stream(original_url, "admin", request),
                        403,
                    )

    def test_startup_sweeps_stale_vod_cache_directories(self):
        """Entering the lifespan runs the startup work exactly once."""
        try:
            from starlette.testclient import TestClient
        except ImportError:  # pragma: no cover - testclient ships with starlette
            self.skipTest("starlette testclient is unavailable")

        with patch("frigate.api.web.vod_native.start_cache_maintenance") as sweep:
            app = create_native_web_app(MagicMock(), 5000)

            with TestClient(app):
                sweep.assert_called_once()


class TestNativeProxyIdentity(unittest.IsolatedAsyncioTestCase):
    def _api_app(self):
        return SimpleNamespace(
            frigate_config=SimpleNamespace(
                auth=SimpleNamespace(enabled=False),
                proxy=SimpleNamespace(
                    auth_secret="proxy-secret",
                    header_map=SimpleNamespace(
                        user="x-auth-user",
                        role="x-auth-groups",
                    ),
                ),
            )
        )

    @staticmethod
    def _scope(secret: str | None = None) -> dict:
        headers = [(b"host", b"frigate.local"), (b"x-auth-user", b"alice")]
        if secret is not None:
            headers.append((b"x-proxy-secret", secret.encode()))
        return {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/config",
            "raw_path": b"/api/config",
            "query_string": b"",
            "headers": headers,
        }

    async def test_mapped_identity_requires_the_proxy_secret(self) -> None:
        auth = InProcessAuth(self._api_app())

        with patch("frigate.api.web.auth_check") as auth_check:
            for supplied in (None, "wrong-secret"):
                with self.subTest(secret=supplied):
                    status, headers, body = await auth(self._scope(supplied), 8971)
                    self.assertEqual((status, headers, body), (401, [], b""))
            auth_check.assert_not_called()

    async def test_valid_proxy_secret_forwards_only_configured_identity(self) -> None:
        auth = InProcessAuth(self._api_app())
        observed_headers: dict[str, str] = {}

        def authorize(request: Request) -> Response:
            nonlocal observed_headers
            observed_headers = dict(request.headers)
            return Response(
                status_code=202,
                headers={"remote-user": request.headers["x-auth-user"]},
            )

        scope = self._scope("proxy-secret")
        scope["headers"].extend(
            [
                (b"remote-role", b"admin"),
                (b"x-unrelated-identity", b"spoofed"),
            ]
        )
        with patch("frigate.api.web.auth_check", side_effect=authorize):
            status, headers, _body = await auth(scope, 8971)

        self.assertEqual(status, 202)
        self.assertIn((b"remote-user", b"alice"), headers)
        self.assertEqual(observed_headers["x-proxy-secret"], "proxy-secret")
        self.assertEqual(observed_headers["x-auth-user"], "alice")
        self.assertNotIn("remote-role", observed_headers)
        self.assertNotIn("x-unrelated-identity", observed_headers)

    def test_client_proxy_headers_are_removed_before_dispatch(self) -> None:
        scope = self._scope("proxy-secret")
        scope["headers"].extend(
            [(b"remote-user", b"mallory"), (b"remote-role", b"admin")]
        )

        stripped = NativeAuthMiddleware._strip_proxy_headers(scope)
        names = {name.lower() for name, _value in stripped}

        self.assertNotIn(b"x-proxy-secret", names)
        self.assertNotIn(b"remote-user", names)
        self.assertNotIn(b"remote-role", names)
        self.assertIn(b"x-auth-user", names)


class TestNeedsAuth(unittest.TestCase):
    def test_login_flow_is_exempt(self):
        """The login endpoints must answer before a session exists.

        They all start with the protected ``/api/`` prefix, so checking the
        prefixes before the exemptions would leave them requiring the very
        session they exist to create, and nobody could log in on a native
        install that serves the web interface on its own port.
        """
        for path in ("/api/login", "/api/logout", "/api/auth/first_time_login"):
            with self.subTest(path=path):
                self.assertFalse(needs_auth(path))

    def test_protected_prefixes_require_auth(self):
        for path in (
            "/api/config",
            "/api",
            "/ws",
            "/vod/front_door/start/0/end/1/index.m3u8",
            "/clips/front_door/event.jpg",
            "/recordings/2024-01-01/00/front_door/00.00.mp4",
            "/exports/clip.mp4",
            "/cache/preview.jpg",
            "/live/mse/front_door",
        ):
            with self.subTest(path=path):
                self.assertTrue(needs_auth(path))

    def test_go2rtc_proxy_requires_auth(self):
        """The proxy reaches go2rtc's API, which reports camera URLs."""
        self.assertTrue(needs_auth("/go2rtc/api/streams"))

    def test_web_app_is_public(self):
        for path in ("/", "/review", "/assets/index.js", "/locales/en.json"):
            with self.subTest(path=path):
                self.assertFalse(needs_auth(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
