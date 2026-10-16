"""Tests for native Windows hardware-stat collection."""

import unittest
from unittest.mock import patch

from frigate.stats.hardware import _hwaccel_hardware, _poll_nvidia, _present_gpu


class TestHardwareStats(unittest.TestCase):
    def test_only_nvidia_windows_presets_select_nvml(self) -> None:
        self.assertEqual(_hwaccel_hardware("preset-windows-nvenc"), "nvidia")
        self.assertEqual(_hwaccel_hardware("-hwaccel cuda"), "nvidia")
        self.assertIsNone(_hwaccel_hardware("preset-windows-d3d11va"))
        self.assertIsNone(_hwaccel_hardware(""))

    def test_present_gpu_uses_windows_hardware_probe(self) -> None:
        with patch(
            "frigate.stats.hardware._present_hardware",
            return_value={"cpu", "onnx:nvidia"},
        ):
            self.assertEqual(_present_gpu(), "nvidia")

        with patch("frigate.stats.hardware._present_hardware", return_value={"cpu"}):
            self.assertIsNone(_present_gpu())

    def test_nvidia_stats_are_normalized(self) -> None:
        raw = {
            0: {
                "name": "NVIDIA GeForce",
                "gpu": "12.345",
                "mem": "23.456",
                "enc": "1",
                "dec": "2",
                "temp": "55",
            }
        }
        with patch("frigate.stats.hardware.get_nvidia_gpu_stats", return_value=raw):
            result = _poll_nvidia(None)  # type: ignore[arg-type]

        self.assertTrue(result.ok)
        self.assertEqual(result.gpu["NVIDIA GeForce"]["gpu"], "12.35%")
        self.assertEqual(result.gpu["NVIDIA GeForce"]["mem"], "23.46%")
        self.assertEqual(result.gpu["NVIDIA GeForce"]["temp"], "55")

    def test_empty_nvml_result_enters_cooldown(self) -> None:
        with patch("frigate.stats.hardware.get_nvidia_gpu_stats", return_value={}):
            result = _poll_nvidia(None)  # type: ignore[arg-type]

        self.assertFalse(result.ok)
        self.assertIn("nvidia-gpu", result.gpu)


if __name__ == "__main__":
    unittest.main()
