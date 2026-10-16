"""Tests for parsing detection hardware device strings."""

import unittest
from unittest.mock import patch

from frigate.detectors.detection_api import DetectionApi
from frigate.detectors.detector_config import ModelConfig
from frigate.detectors.device import (
    LINUX_ONLY_DETECTORS,
    DeviceParseError,
    build_detector_config,
    config_types,
    parse_device,
    runner_names,
)
from frigate.util.runtime_deps import RuntimeDependencyError, RuntimeManifest


class TestParseDevice(unittest.TestCase):
    def setUp(self) -> None:
        # These cases exercise cross-platform parsing semantics, not the
        # Windows allowlist; the Windows-specific cases patch it explicitly.
        platform_patch = patch("frigate.detectors.device.IS_WINDOWS", False)
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_bare_detector_has_no_device(self):
        spec = parse_device("cpu")

        self.assertEqual(spec.detector, "cpu")
        self.assertIsNone(spec.device)

    def test_device_is_everything_after_the_first_colon(self):
        spec = parse_device("edgetpu:pci:0")

        self.assertEqual(spec.detector, "edgetpu")
        self.assertEqual(spec.device, "pci:0")

    def test_trailing_colon_keeps_an_empty_device(self):
        # an empty edgetpu device selects a native Coral
        spec = parse_device("edgetpu:")

        self.assertEqual(spec.detector, "edgetpu")
        self.assertEqual(spec.device, "")

    def test_unknown_detector_is_rejected(self):
        with self.assertRaises(DeviceParseError):
            parse_device("not_a_detector:0")

    def _parse_without_plugins(self, raw: str):
        """Parse as if no plugin registered the configured detector type.

        The Linux-only detector check must not depend on whether its plugin
        happened to import; unknown-name and import-failure cases use this to
        cover the other error messages.
        """
        with patch.dict("frigate.detectors.device.config_types", {}, clear=True):
            return parse_device(raw)

    @patch("frigate.detectors.device.IS_WINDOWS", True)
    def test_linux_only_detector_is_named_on_windows(self):
        """A Linux-only detector should explain why it cannot run on Windows.

        The actionable platform message has to win whether the detector plugin
        failed to import or registered a config type without its vendor SDK.
        """
        with self.assertRaises(DeviceParseError) as raised:
            self._parse_without_plugins("edgetpu:usb:0")

        self.assertIn("only available on Linux", str(raised.exception))
        self.assertIn("cpu, onnx, openvino, zmq", str(raised.exception))

    @patch("frigate.detectors.device.IS_WINDOWS", True)
    def test_windows_detectors_still_parse_on_windows(self):
        for raw in ("cpu", "onnx:0", "openvino:GPU", "zmq"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_device(raw).detector, raw.partition(":")[0])

    @patch("frigate.detectors.device.IS_WINDOWS", True)
    def test_importable_linux_plugin_is_still_rejected_on_windows(self):
        # axengine's plugin module imports without its vendor SDK, so it is in
        # config_types even though its device runtime is Linux-only.
        self.assertIn("axengine", config_types)

        for detector in LINUX_ONLY_DETECTORS:
            with self.subTest(detector=detector):
                with self.assertRaises(DeviceParseError) as raised:
                    parse_device(detector)

                self.assertIn("only available on Linux", str(raised.exception))

    def test_direct_linux_runtime_installation_is_blocked_on_windows(self):
        manifest = RuntimeManifest(name="axengine", version="test", artifacts=())

        with (
            patch("frigate.detectors.detection_api.IS_WINDOWS", True),
            patch("frigate.detectors.detection_api.ensure_installed") as install,
            patch.object(DetectionApi, "type_key", "axengine", create=True),
            patch.object(DetectionApi, "runtime_manifest", manifest),
        ):
            with self.assertRaises(RuntimeDependencyError) as raised:
                DetectionApi.ensure_dependencies()

        self.assertIn("not supported on native Windows", str(raised.exception))
        install.assert_not_called()

    @patch("frigate.detectors.device.IS_WINDOWS", True)
    def test_unknown_name_on_windows_reports_the_windows_detectors(self):
        with self.assertRaises(DeviceParseError) as raised:
            self._parse_without_plugins("not_a_detector:0")

        self.assertIn("Detectors available on Windows", str(raised.exception))

    @patch("frigate.detectors.device.IS_WINDOWS", False)
    def test_linux_detector_message_is_unchanged_on_linux(self):
        with self.assertRaises(DeviceParseError) as raised:
            self._parse_without_plugins("edgetpu:usb:0")

        # the plugin may not even be installed here; either way the message
        # must not claim Windows
        self.assertNotIn("Windows", str(raised.exception))

    def test_failed_runtime_import_is_reported(self):
        """A known detector whose plugin failed to import says why."""
        with patch.dict(
            "frigate.detectors.detector_types.failed_plugin_imports",
            {"fake_det": "No module named 'some_runtime'"},
        ):
            with self.assertRaises(DeviceParseError) as raised:
                self._parse_without_plugins("fake_det:0")

        self.assertIn("runtime is not usable", str(raised.exception))
        self.assertIn("No module named 'some_runtime'", str(raised.exception))

    @patch("frigate.detectors.device.IS_WINDOWS", True)
    def test_linux_only_detector_message_wins_over_import_failure_on_windows(self):
        """The Windows message is more actionable than the import error."""
        with patch.dict(
            "frigate.detectors.detector_types.failed_plugin_imports",
            {"edgetpu_tfl": "No module named 'pycoral'"},
        ):
            with self.assertRaises(DeviceParseError) as raised:
                self._parse_without_plugins("edgetpu:usb:0")

        self.assertIn("only available on Linux", str(raised.exception))

    def test_device_that_the_detector_cannot_use_is_rejected(self):
        # tensorrt takes a gpu index
        with self.assertRaises(DeviceParseError):
            parse_device("tensorrt:the-fast-one")


class TestBuildDetectorConfig(unittest.TestCase):
    def setUp(self) -> None:
        platform_patch = patch("frigate.detectors.device.IS_WINDOWS", False)
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def _build(self, raw: str):
        return build_detector_config(parse_device(raw), ModelConfig())

    def test_device_lands_on_the_detector_field(self):
        for raw, expected in (
            ("edgetpu:usb", "usb"),
            ("edgetpu:pci:1", "pci:1"),
            ("openvino:GPU.1", "GPU.1"),
            ("onnx:CPU", "CPU"),
            ("memryx:PCIe:0", "PCIe:0"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self._build(raw).device, expected)

    def test_detectors_that_name_the_field_something_else(self):
        self.assertEqual(self._build("cpu:4").num_threads, 4)
        self.assertEqual(self._build("rknn:2").num_cores, 2)
        self.assertEqual(
            self._build("zmq:tcp://host.docker.internal:5555").endpoint,
            "tcp://host.docker.internal:5555",
        )

    def test_device_is_coerced_to_the_detector_field_type(self):
        self.assertEqual(self._build("tensorrt:1").device, 1)

    def test_omitted_device_falls_back_to_the_detector_default(self):
        self.assertEqual(self._build("cpu").num_threads, 3)
        self.assertEqual(self._build("rknn").num_cores, 0)
        self.assertEqual(self._build("zmq").endpoint, "ipc:///tmp/cache/zmq_detector")
        self.assertEqual(self._build("openvino").device, "AUTO")
        self.assertIsNone(self._build("edgetpu").device)

    def test_the_model_is_attached(self):
        model = ModelConfig(path="/cpu_model.tflite")

        self.assertIs(build_detector_config(parse_device("cpu"), model).model, model)


class TestRknnPlatform(unittest.TestCase):
    def test_get_soc_rejects_windows_before_reading_device_tree(self) -> None:
        try:
            from frigate.detectors.plugins.rknn import Rknn
        except ImportError as err:
            self.skipTest(f"RKNN plugin dependencies are unavailable: {err}")

        detector = object.__new__(Rknn)

        with (
            patch("frigate.detectors.plugins.rknn.IS_WINDOWS", True),
            patch("builtins.open") as open_file,
        ):
            with self.assertRaisesRegex(RuntimeError, "only available on Linux"):
                detector.get_soc()

        open_file.assert_not_called()


class TestRunnerNames(unittest.TestCase):
    def setUp(self) -> None:
        platform_patch = patch("frigate.detectors.device.IS_WINDOWS", False)
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_unique_devices_keep_their_name(self):
        devices = [parse_device("edgetpu:pci:0"), parse_device("edgetpu:pci:1")]

        self.assertEqual(runner_names(devices), ["edgetpu:pci:0", "edgetpu:pci:1"])

    def test_repeated_devices_are_numbered(self):
        devices = [parse_device("openvino:GPU")] * 3

        self.assertEqual(
            runner_names(devices),
            ["openvino:GPU", "openvino:GPU#2", "openvino:GPU#3"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
