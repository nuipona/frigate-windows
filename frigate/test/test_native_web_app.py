"""The native web server, which replaces nginx on Windows."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from frigate.api.auth import deny_response_for_go2rtc_stream
from frigate.api.web import build_auth_scope, create_native_web_app, needs_auth


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
