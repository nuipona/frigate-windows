"""Tests for native Windows hardware-decoding recommendations."""

import unittest
from unittest.mock import patch

from frigate.util import hwaccel


class TestIntelGeneration(unittest.TestCase):
    def test_numbered_generation_is_detected(self):
        self.assertEqual(
            hwaccel._generation_of("13th Gen Intel(R) Core(TM) i5-13500"), 13
        )

    def test_core_ultra_is_treated_as_current(self):
        self.assertEqual(
            hwaccel._generation_of("Intel(R) Core(TM) Ultra 7 155H"),
            hwaccel.INTEL_GEN_LATEST,
        )

    def test_unknown_processor_has_no_generation(self):
        self.assertIsNone(hwaccel._generation_of("Intel(R) Core(TM) i7-8700K"))
        self.assertIsNone(hwaccel._generation_of(""))


class TestAvailableFamilies(unittest.TestCase):
    def _options(
        self,
        adapters: list[str],
        vendors: set[str],
        *,
        cpu: str = "",
        detector_key: str | None = None,
        codecs: set[str] | None = None,
    ):
        with (
            patch.object(hwaccel, "get_display_adapters", return_value=adapters),
            patch.object(hwaccel, "get_display_adapter_vendors", return_value=vendors),
            patch.object(hwaccel, "get_cpu_brand", return_value=cpu),
        ):
            return hwaccel.hwaccel_options(detector_key, codecs)

    def test_no_display_adapter_offers_nothing(self):
        recommended, available = self._options([], set())

        self.assertEqual(recommended, "")
        self.assertEqual(available, [])

    def test_nvidia_prefers_nvenc_then_d3d11va(self):
        recommended, available = self._options(["NVIDIA RTX 4090"], {"nvidia"})

        self.assertEqual(recommended, "windows-nvenc")
        self.assertEqual(
            [family.key for family in available],
            ["windows-nvenc", "windows-d3d11va"],
        )

    def test_recent_intel_prefers_qsv_for_known_codec(self):
        recommended, available = self._options(
            ["Intel Arc"],
            {"intel"},
            cpu="13th Gen Intel(R) Core(TM) i5-13500",
            codecs={"h264"},
        )

        self.assertEqual(recommended, "windows-qsv")
        self.assertEqual(
            [family.key for family in available],
            ["windows-qsv", "windows-d3d11va"],
        )

    def test_unknown_intel_prefers_vendor_neutral_decoder(self):
        recommended, available = self._options(
            ["Intel UHD"], {"intel"}, codecs={"h265"}
        )

        self.assertEqual(recommended, "windows-d3d11va")
        self.assertEqual(
            [family.key for family in available],
            ["windows-d3d11va", "windows-qsv"],
        )

    def test_amd_uses_d3d11va(self):
        recommended, available = self._options(["AMD Radeon"], {"amd"})

        self.assertEqual(recommended, "windows-d3d11va")
        self.assertEqual(
            [family.key for family in available], ["windows-d3d11va"]
        )

    def test_unknown_vendor_still_uses_d3d11va(self):
        recommended, _available = self._options(["Basic Display Adapter"], set())
        self.assertEqual(recommended, "windows-d3d11va")

    def test_openvino_detector_biases_qsv(self):
        recommended, available = self._options(
            ["Intel UHD"],
            {"intel"},
            detector_key="openvino:GPU",
            codecs={"h264"},
        )

        self.assertEqual(recommended, "windows-qsv")
        self.assertEqual(available[0].key, "windows-qsv")

    def test_nvidia_detector_biases_nvenc(self):
        recommended, available = self._options(
            ["NVIDIA RTX", "Intel UHD"],
            {"nvidia", "intel"},
            detector_key="onnx:nvidia",
            codecs={"h264"},
        )

        self.assertEqual(recommended, "windows-nvenc")
        self.assertEqual(available[0].key, "windows-nvenc")

    def test_unknown_codec_drops_qsv_but_keeps_agnostic_families(self):
        recommended, available = self._options(
            ["Intel UHD"], {"intel"}, codecs={"mjpeg"}
        )

        self.assertEqual(recommended, "windows-d3d11va")
        self.assertEqual(
            [family.key for family in available], ["windows-d3d11va"]
        )

    def test_hevc_alias_is_supported_by_qsv(self):
        _recommended, available = self._options(
            ["Intel UHD"],
            {"intel"},
            cpu="13th Gen Intel(R) Core(TM) i5-13500",
            codecs={"hevc"},
        )

        self.assertIn("windows-qsv", [family.key for family in available])

    def test_unknown_codec_set_prefers_codec_agnostic_family(self):
        recommended, _available = self._options(
            ["Intel UHD"],
            {"intel"},
            cpu="13th Gen Intel(R) Core(TM) i5-13500",
        )

        self.assertEqual(recommended, "windows-d3d11va")


class TestAutomaticPreset(unittest.TestCase):
    def _preset(
        self,
        sources=None,
        *,
        adapters: list[str] | None = None,
        vendors: set[str] | None = None,
    ) -> str:
        with (
            patch.object(
                hwaccel, "get_display_adapters", return_value=adapters or []
            ),
            patch.object(
                hwaccel, "get_display_adapter_vendors", return_value=vendors or set()
            ),
        ):
            return hwaccel.windows_hwaccel_preset(sources)

    def test_go2rtc_cuda_source_wins(self):
        sources = [{"name": "OK", "url": "ffmpeg:camera#hardware=cuda"}]
        self.assertEqual(self._preset(sources), "preset-windows-nvenc")

    def test_go2rtc_qsv_source_is_detected(self):
        sources = [{"name": "OK", "url": "ffmpeg:camera#hardware=qsv"}]
        self.assertEqual(self._preset(sources), "preset-windows-qsv")

    def test_failed_go2rtc_sources_are_ignored(self):
        sources = [{"name": "ERROR", "url": "ffmpeg:camera#hardware=cuda"}]
        self.assertEqual(self._preset(sources), "")

    def test_nvidia_adapter_uses_nvenc(self):
        self.assertEqual(
            self._preset(adapters=["NVIDIA RTX"], vendors={"nvidia"}),
            "preset-windows-nvenc",
        )

    def test_intel_adapter_uses_qsv(self):
        self.assertEqual(
            self._preset(adapters=["Intel UHD"], vendors={"intel"}),
            "preset-windows-qsv",
        )

    def test_other_adapter_uses_d3d11va(self):
        self.assertEqual(
            self._preset(adapters=["AMD Radeon"], vendors={"amd"}),
            "preset-windows-d3d11va",
        )

    def test_no_adapter_disables_automatic_acceleration(self):
        self.assertEqual(self._preset(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
