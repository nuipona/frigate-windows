"""The native web server, which replaces nginx on Windows."""

import unittest
from unittest.mock import MagicMock, patch

from frigate.api.web import create_native_web_app


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
