"""The native web server, which replaces nginx on Windows."""

import unittest
from unittest.mock import MagicMock, patch

from frigate.api.web import create_native_web_app, needs_auth


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
