"""Tests for the Windows hardware detection helpers.

The Windows only APIs cannot be called here, so what is covered is everything
that determines what Frigate does with their result: vendor classification, the
preset an accelerator maps to, and the guarantee that every preset auto
detection can produce is a preset that exists.
"""

import unittest
from unittest.mock import patch

from frigate.ffmpeg_presets import (
    PRESETS_HW_ACCEL_DECODE,
    windows_decode_presets,
)
from frigate.util import hwaccel
from frigate.util.platform import (
    classify_adapter_vendors,
    get_cpu_brand,
    get_display_adapter_vendors,
    get_display_adapters,
)


class TestAdapterVendorClassification(unittest.TestCase):
    def test_vendors_are_recognized_in_adapter_names(self):
        self.assertEqual(
            classify_adapter_vendors(
                [
                    "NVIDIA GeForce RTX 4070",
                    "AMD Radeon RX 6800",
                    "Intel(R) UHD Graphics 770",
                ]
            ),
            {"nvidia", "amd", "intel"},
        )

    def test_alternate_amd_names_are_recognized(self):
        self.assertEqual(classify_adapter_vendors(["Radeon RX 580"]), {"amd"})
        self.assertEqual(
            classify_adapter_vendors(["Advanced Micro Devices, Inc. Adapter"]),
            {"amd"},
        )

    def test_unknown_adapters_are_not_guessed(self):
        self.assertEqual(
            classify_adapter_vendors(["Microsoft Basic Display Adapter"]), set()
        )

    def test_no_adapters_on_this_platform(self):
        # the enumeration is Windows only, so a POSIX run must return empty
        self.assertEqual(get_display_adapters(), [])
        self.assertEqual(get_display_adapter_vendors(), set())
        self.assertEqual(get_cpu_brand(), "")


class TestWindowsHwaccelPreset(unittest.TestCase):
    def test_go2rtc_accelerators_map_to_windows_presets(self):
        cases = [
            ("-hwaccel cuda", "preset-windows-nvenc"),
            ("-hwaccel qsv", "preset-windows-qsv"),
            ("-hwaccel d3d11va", "preset-windows-d3d11va"),
            ("-hwaccel dxva2", "preset-windows-dxva2"),
        ]

        for url, expected in cases:
            with self.subTest(url=url):
                sources = [{"name": "OK", "url": url}]
                self.assertEqual(hwaccel.windows_hwaccel_preset(sources), expected)

    def test_failed_accelerators_are_ignored(self):
        sources = [{"name": "FAILED", "url": "-hwaccel cuda"}]

        with patch.object(hwaccel, "get_display_adapters", return_value=[]):
            self.assertEqual(hwaccel.windows_hwaccel_preset(sources), "")

    def test_specific_accelerator_wins_over_the_generic_one(self):
        sources = [
            {"name": "OK", "url": "-hwaccel d3d11va"},
            {"name": "OK", "url": "-hwaccel cuda"},
        ]
        self.assertEqual(
            hwaccel.windows_hwaccel_preset(sources), "preset-windows-nvenc"
        )

    def test_adapters_are_used_when_go2rtc_is_not_running(self):
        with (
            patch.object(
                hwaccel,
                "get_display_adapters",
                return_value=["NVIDIA GeForce RTX 3060"],
            ),
            patch.object(
                hwaccel, "get_display_adapter_vendors", return_value={"nvidia"}
            ),
        ):
            self.assertEqual(hwaccel.windows_hwaccel_preset([]), "preset-windows-nvenc")

    def test_intel_gpu_falls_back_to_qsv(self):
        with (
            patch.object(
                hwaccel,
                "get_display_adapters",
                return_value=["Intel(R) UHD Graphics 770"],
            ),
            patch.object(
                hwaccel, "get_display_adapter_vendors", return_value={"intel"}
            ),
        ):
            self.assertEqual(hwaccel.windows_hwaccel_preset([]), "preset-windows-qsv")

    def test_unknown_gpu_falls_back_to_d3d11va(self):
        # every Windows GPU decodes through D3D11VA, so an AMD or an
        # unrecognized adapter still gets hardware decoding
        with (
            patch.object(hwaccel, "get_display_adapters", return_value=["Some GPU"]),
            patch.object(hwaccel, "get_display_adapter_vendors", return_value={"amd"}),
        ):
            self.assertEqual(
                hwaccel.windows_hwaccel_preset([]), "preset-windows-d3d11va"
            )

    def test_no_gpu_returns_nothing(self):
        with patch.object(hwaccel, "get_display_adapters", return_value=[]):
            self.assertEqual(hwaccel.windows_hwaccel_preset([]), "")

    def test_every_detected_preset_exists(self):
        sources = [
            {"name": "OK", "url": url}
            for url in (
                "-hwaccel cuda",
                "-hwaccel qsv",
                "-hwaccel d3d11va",
                "-hwaccel dxva2",
            )
        ]

        for source in sources:
            preset = hwaccel.windows_hwaccel_preset([source])
            self.assertIn(preset, PRESETS_HW_ACCEL_DECODE)

        for preset in windows_decode_presets():
            self.assertIn(preset, PRESETS_HW_ACCEL_DECODE)


class TestWindowsHwaccelFamilies(unittest.TestCase):
    def test_nvidia_family_uses_the_windows_preset(self):
        families = hwaccel._windows_families("onnx:nvidia")
        self.assertEqual(families[0].key, "windows-nvenc")
        self.assertEqual(families[0].presets[hwaccel.ANY_CODEC], "preset-windows-nvenc")

    def test_intel_families_offer_qsv_then_d3d11va(self):
        with patch.object(hwaccel, "_intel_generation", return_value=13):
            families = hwaccel._windows_families("openvino:GPU")

        self.assertEqual(
            [family.key for family in families], ["windows-qsv", "windows-d3d11va"]
        )
        self.assertEqual(families[0].presets["h264"], "preset-windows-qsv-h264")
        self.assertEqual(families[0].presets["h265"], "preset-windows-qsv-h265")

    def test_old_intel_families_prefer_d3d11va(self):
        with patch.object(hwaccel, "_intel_generation", return_value=7):
            families = hwaccel._windows_families("openvino:GPU")

        self.assertEqual(
            [family.key for family in families], ["windows-d3d11va", "windows-qsv"]
        )

    def test_windows_never_offers_a_linux_only_family(self):
        for key in ("onnx:nvidia", "openvino:GPU", hwaccel.WINDOWS_DISPLAY_ADAPTER):
            with patch.object(hwaccel, "IS_WINDOWS", True):
                families = hwaccel._families(key, None)

            for family in families:
                for preset in family.presets.values():
                    self.assertTrue(
                        preset.startswith("preset-windows-")
                        or preset in ("preset-amd-amf", "preset-vulkan"),
                        f"{preset} cannot decode on Windows",
                    )

    def test_intel_generation_uses_the_windows_cpu_name(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(
                hwaccel,
                "get_cpu_brand",
                return_value="13th Gen Intel(R) Core(TM) i5-13500",
            ),
        ):
            self.assertEqual(hwaccel._intel_generation(), 13)

    def test_intel_ultra_is_treated_as_the_newest_generation(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(
                hwaccel, "get_cpu_brand", return_value="Intel(R) Core(TM) Ultra 7 155H"
            ),
        ):
            self.assertEqual(hwaccel._intel_generation(), hwaccel.INTEL_GEN_LATEST)

    def test_unknown_cpu_name_has_no_generation(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(hwaccel, "get_cpu_brand", return_value="AMD Ryzen 9 7950X"),
        ):
            self.assertIsNone(hwaccel._intel_generation())


class TestWindowsDecodeHardware(unittest.TestCase):
    def test_display_adapter_is_added_to_the_hardware_list(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(hwaccel.hardware_prober, "probe", return_value=[]),
            patch.object(hwaccel, "get_display_adapters", return_value=["Some GPU"]),
        ):
            hardware = hwaccel._decode_hardware(None)

        self.assertIn(hwaccel.WINDOWS_DISPLAY_ADAPTER, hardware)

    def test_no_gpu_means_no_windows_hardware(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(hwaccel.hardware_prober, "probe", return_value=[]),
            patch.object(hwaccel, "get_display_adapters", return_value=[]),
        ):
            hardware = hwaccel._decode_hardware(None)

        self.assertEqual(hardware, [])

    def test_proc_and_sys_reads_are_skipped_on_windows(self):
        with (
            patch.object(hwaccel, "IS_WINDOWS", True),
            patch.object(hwaccel, "_has_lighter_media") as lighter,
            patch.object(hwaccel, "_is_raspberry_pi") as raspberry,
        ):
            hwaccel._decode_hardware(None)

        lighter.assert_not_called()
        raspberry.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
