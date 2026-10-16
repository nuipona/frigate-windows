"""Tests for native Windows detection-hardware discovery."""

import unittest
from unittest.mock import MagicMock, patch

from frigate.detectors import hardware
from frigate.detectors.hardware import HardwareProber, HardwareUnit


class TestCpuHardware(unittest.TestCase):
    def test_cpu_is_always_available(self):
        cpu = hardware.detect_cpu()

        self.assertEqual(cpu.key, "cpu")
        self.assertEqual(cpu.detector, "cpu")
        self.assertEqual(cpu.count, 1)
        self.assertEqual(cpu.units, [HardwareUnit(device="cpu", label="CPU")])
        self.assertTrue(cpu.unlimited)


class TestOpenVinoHardware(unittest.TestCase):
    @patch(
        "frigate.detectors.hardware._openvino_available_devices",
        return_value=["CPU", "GPU.0", "GPU.1", "NPU"],
    )
    def test_devices_are_discovered_through_openvino(self, _available):
        gpu = hardware.detect_intel_gpu()
        npu = hardware.detect_intel_npu()

        self.assertEqual(
            [unit.device for unit in gpu.units],
            ["openvino:GPU.0", "openvino:GPU.1"],
        )
        self.assertEqual([unit.device for unit in npu.units], ["openvino:NPU"])
        self.assertEqual(gpu.detector, "openvino")
        self.assertEqual(npu.detector, "openvino")

    @patch(
        "frigate.detectors.hardware._openvino_available_devices",
        return_value=["CPU"],
    )
    def test_missing_accelerators_are_ignored(self, _available):
        self.assertIsNone(hardware.detect_intel_gpu())
        self.assertIsNone(hardware.detect_intel_npu())

    @patch("openvino.Core", side_effect=RuntimeError("runtime unavailable"))
    def test_openvino_enumeration_failure_is_empty(self, _core):
        self.assertEqual(hardware._openvino_available_devices(), [])


class TestDirectMlHardware(unittest.TestCase):
    @patch(
        "frigate.detectors.hardware.get_display_adapters",
        return_value=["Intel Arc", "AMD Radeon"],
    )
    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"DmlExecutionProvider", "CPUExecutionProvider"},
    )
    def test_directml_adapters_are_indexed(self, _providers, _adapters):
        gpu = hardware.detect_directml_gpu()

        self.assertEqual(gpu.key, "onnx:directml")
        self.assertEqual(
            [unit.device for unit in gpu.units], ["onnx:0", "onnx:1"]
        )
        self.assertEqual(
            [unit.label for unit in gpu.units], ["Intel Arc", "AMD Radeon"]
        )

    @patch("frigate.detectors.hardware.get_display_adapters")
    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"CPUExecutionProvider"},
    )
    def test_directml_is_ignored_without_provider(self, _providers, adapters):
        self.assertIsNone(hardware.detect_directml_gpu())
        adapters.assert_not_called()

    @patch("frigate.detectors.hardware.get_display_adapters", return_value=[])
    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"DmlExecutionProvider"},
    )
    def test_directml_is_ignored_without_display_adapters(
        self, _providers, _adapters
    ):
        self.assertIsNone(hardware.detect_directml_gpu())


class TestNvidiaHardware(unittest.TestCase):
    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"CPUExecutionProvider"},
    )
    def test_nvidia_is_ignored_without_cuda_provider(self, _providers):
        self.assertIsNone(hardware.detect_nvidia_gpu())

    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"CUDAExecutionProvider"},
    )
    @patch("py3nvml.py3nvml.nvmlDeviceGetName", side_effect=[b"RTX 3060", "RTX 4090"])
    @patch("py3nvml.py3nvml.nvmlDeviceGetHandleByIndex", side_effect=["gpu0", "gpu1"])
    @patch("py3nvml.py3nvml.nvmlDeviceGetCount", return_value=2)
    @patch("py3nvml.py3nvml.nvmlInit")
    def test_nvidia_devices_are_indexed(
        self, _init, _count, _handle, _name, _providers
    ):
        gpu = hardware.detect_nvidia_gpu()

        self.assertEqual(gpu.key, "onnx:nvidia")
        self.assertEqual(gpu.name, "NVIDIA GPU")
        self.assertEqual(
            [unit.device for unit in gpu.units], ["onnx:0", "onnx:1"]
        )
        self.assertEqual(
            [unit.label for unit in gpu.units], ["RTX 3060", "RTX 4090"]
        )

    @patch(
        "frigate.detectors.hardware._onnx_providers",
        return_value={"CUDAExecutionProvider"},
    )
    @patch("py3nvml.py3nvml.nvmlInit", side_effect=RuntimeError("driver missing"))
    def test_nvml_failure_is_ignored(self, _init, _providers):
        self.assertIsNone(hardware.detect_nvidia_gpu())


class TestHardwareProber(unittest.TestCase):
    def test_uses_only_native_windows_probes(self):
        first = MagicMock(
            return_value=hardware._hardware(
                "cpu", "cpu", "CPU", [HardwareUnit(device="cpu", label="CPU")]
            )
        )
        second = MagicMock(return_value=None)

        with patch.object(hardware, "WINDOWS_PROBES", (first, second)):
            found = HardwareProber().probe()

        first.assert_called_once_with()
        second.assert_called_once_with()
        self.assertEqual([item.key for item in found], ["cpu"])

    def test_a_failing_probe_does_not_break_the_rest(self):
        broken = MagicMock(side_effect=RuntimeError("probe failed"))
        broken.__name__ = "broken_probe"
        cpu = MagicMock(return_value=hardware.detect_cpu())

        with patch.object(hardware, "WINDOWS_PROBES", (broken, cpu)):
            found = HardwareProber().probe()

        self.assertEqual([item.key for item in found], ["cpu"])

    def test_result_is_cached_until_refreshed(self):
        probe = MagicMock(return_value=hardware.detect_cpu())

        with patch.object(hardware, "WINDOWS_PROBES", (probe,)):
            prober = HardwareProber()
            first = prober.probe()
            second = prober.probe()
            refreshed = prober.probe(refresh=True)

        self.assertIs(first, second)
        self.assertIsNot(second, refreshed)
        self.assertEqual(probe.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
