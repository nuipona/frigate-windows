"""Tests for native Windows detector device strings."""

import unittest
from unittest.mock import patch

from frigate.detectors.detector_config import ModelConfig
from frigate.detectors.device import (
    SUPPORTED_DETECTORS,
    DeviceParseError,
    build_detector_config,
    parse_device,
    runner_names,
)


class TestParseDevice(unittest.TestCase):
    def test_supported_detectors_parse(self):
        for raw in ("cpu", "onnx:0", "openvino:GPU", "zmq"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_device(raw).detector, raw.partition(":")[0])

    def test_device_keeps_everything_after_first_colon(self):
        spec = parse_device("zmq:tcp://camera-host:5555")

        self.assertEqual(spec.detector, "zmq")
        self.assertEqual(spec.device, "tcp://camera-host:5555")

    def test_unknown_detector_reports_windows_allowlist(self):
        with self.assertRaises(DeviceParseError) as raised:
            parse_device("edgetpu:usb")

        for detector in SUPPORTED_DETECTORS:
            self.assertIn(detector, str(raised.exception))

    def test_supported_runtime_import_failure_is_reported(self):
        with (
            patch.dict(
                "frigate.detectors.device.config_types", {}, clear=True
            ),
            patch.dict(
                "frigate.detectors.device.failed_plugin_imports",
                {"onnx": "missing DirectML runtime"},
                clear=True,
            ),
        ):
            with self.assertRaises(DeviceParseError) as raised:
                parse_device("onnx")

        self.assertIn("missing DirectML runtime", str(raised.exception))

    def test_invalid_typed_device_is_rejected(self):
        with self.assertRaises(DeviceParseError):
            parse_device("cpu:many")


class TestBuildDetectorConfig(unittest.TestCase):
    def test_device_lands_on_detector_field(self):
        self.assertEqual(
            build_detector_config(parse_device("cpu:4"), None).num_threads,
            4,
        )
        self.assertEqual(
            build_detector_config(parse_device("openvino:GPU.1"), None).device,
            "GPU.1",
        )
        self.assertEqual(
            build_detector_config(
                parse_device("zmq:tcp://camera-host:5555"), None
            ).endpoint,
            "tcp://camera-host:5555",
        )

    def test_model_is_attached(self):
        model = ModelConfig(path=r"C:\Frigate\models\detector.tflite")

        self.assertIs(build_detector_config(parse_device("cpu"), model).model, model)


class TestRunnerNames(unittest.TestCase):
    def test_unique_devices_keep_names(self):
        devices = [parse_device("onnx:0"), parse_device("onnx:1")]
        self.assertEqual(runner_names(devices), ["onnx:0", "onnx:1"])

    def test_repeated_devices_are_numbered(self):
        devices = [parse_device("openvino:GPU")] * 3
        self.assertEqual(
            runner_names(devices),
            ["openvino:GPU", "openvino:GPU#2", "openvino:GPU#3"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
