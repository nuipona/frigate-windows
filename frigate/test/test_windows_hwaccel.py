"""Tests for native Windows FFmpeg hardware recommendations."""

import unittest
from unittest.mock import patch

from frigate.util import hwaccel


class TestIntelGeneration(unittest.TestCase):
    def test_numbered_generation(self) -> None:
        self.assertEqual(
            hwaccel._generation_of("12th Gen Intel(R) Core(TM) i7-12700"), 12
        )

    def test_core_ultra_is_latest(self) -> None:
        self.assertEqual(
            hwaccel._generation_of("Intel(R) Core(TM) Ultra 7 155H"),
            hwaccel.INTEL_GEN_LATEST,
        )

    def test_unknown_name(self) -> None:
        self.assertIsNone(hwaccel._generation_of("Intel Xeon"))


class TestWindowsFamilies(unittest.TestCase):
    def _adapters(self, vendors: set[str]):
        return (
            patch.object(hwaccel, "get_display_adapters", return_value=["GPU"]),
            patch.object(hwaccel, "get_display_adapter_vendors", return_value=vendors),
        )

    def test_nvidia_prefers_nvenc(self) -> None:
        adapters, vendors = self._adapters({"nvidia"})
        with adapters, vendors:
            recommended, available = hwaccel.hwaccel_options("onnx:nvidia")

        self.assertEqual(recommended, "windows-nvenc")
        self.assertEqual(available[0].key, "windows-nvenc")

    def test_modern_intel_prefers_qsv_for_known_codec(self) -> None:
        adapters, vendors = self._adapters({"intel"})
        with (
            adapters,
            vendors,
            patch.object(hwaccel, "_intel_generation", return_value=12),
        ):
            recommended, available = hwaccel.hwaccel_options(
                "openvino:GPU", {"h264"}
            )

        self.assertEqual(recommended, "windows-qsv")
        self.assertEqual(available[0].key, "windows-qsv")

    def test_amd_uses_d3d11va(self) -> None:
        adapters, vendors = self._adapters({"amd"})
        with adapters, vendors:
            self.assertEqual(hwaccel.recommend_hwaccel(), "windows-d3d11va")

    def test_no_adapter_has_no_recommendation(self) -> None:
        with patch.object(hwaccel, "get_display_adapters", return_value=[]):
            self.assertEqual(hwaccel.hwaccel_options(), ("", []))

    def test_hevc_alias_selects_h265_qsv_preset(self) -> None:
        adapters, vendors = self._adapters({"intel"})
        with (
            adapters,
            vendors,
            patch.object(hwaccel, "_intel_generation", return_value=12),
        ):
            _, available = hwaccel.hwaccel_options(codecs={"hevc"})

        qsv = next(family for family in available if family.key == "windows-qsv")
        self.assertEqual(qsv.presets["h265"], "preset-windows-qsv-h265")


class TestAutoPreset(unittest.TestCase):
    def test_go2rtc_cuda_wins(self) -> None:
        sources = [{"name": "OK", "url": "ffmpeg:camera#hardware=cuda"}]
        self.assertEqual(
            hwaccel.windows_hwaccel_preset(sources), "preset-windows-nvenc"
        )

    def test_go2rtc_qsv(self) -> None:
        sources = [{"name": "OK", "url": "ffmpeg:camera#hardware=qsv"}]
        self.assertEqual(
            hwaccel.windows_hwaccel_preset(sources), "preset-windows-qsv"
        )

    def test_display_adapter_fallback(self) -> None:
        with (
            patch.object(hwaccel, "get_display_adapters", return_value=["AMD Radeon"]),
            patch.object(
                hwaccel, "get_display_adapter_vendors", return_value={"amd"}
            ),
        ):
            self.assertEqual(
                hwaccel.windows_hwaccel_preset(), "preset-windows-d3d11va"
            )

    def test_no_adapter_falls_back_to_software(self) -> None:
        with patch.object(hwaccel, "get_display_adapters", return_value=[]):
            self.assertEqual(hwaccel.windows_hwaccel_preset(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
