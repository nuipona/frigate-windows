"""Tests for Windows model-runner device selection and reporting."""

import threading
import unittest
from unittest.mock import MagicMock, patch

from frigate.detectors import detection_runners
from frigate.detectors.detection_runners import (
    CudaGraphRunner,
    ONNXModelRunner,
    OpenVINOModelRunner,
    get_optimized_runner,
    loaded_devices,
    record_loaded_device,
    snapshot_loaded_devices,
)


class TestRunnerDeviceName(unittest.TestCase):
    def _onnx(self, providers: list[str]) -> ONNXModelRunner:
        session = MagicMock()
        session.get_providers.return_value = providers
        return ONNXModelRunner(session, "arcface")

    def test_windows_provider_names(self) -> None:
        self.assertEqual(self._onnx(["DmlExecutionProvider"]).device_name, "DirectML")
        self.assertEqual(self._onnx(["CUDAExecutionProvider"]).device_name, "CUDA")
        self.assertEqual(
            self._onnx(["OpenVINOExecutionProvider"]).device_name, "OpenVINO"
        )
        self.assertEqual(self._onnx(["CPUExecutionProvider"]).device_name, "CPU")
        self.assertEqual(self._onnx([]).device_name, "CPU")

    def test_unknown_provider_has_readable_name(self) -> None:
        self.assertEqual(self._onnx(["CustomExecutionProvider"]).device_name, "Custom")

    def test_cuda_graph_runner(self) -> None:
        runner = CudaGraphRunner(MagicMock(), 0)
        self.assertEqual(runner.device_name, "CUDA")

    def test_openvino_reports_compiled_device(self) -> None:
        runner = OpenVINOModelRunner.__new__(OpenVINOModelRunner)
        runner.compiled_device = "GPU"
        runner.compiled_model = MagicMock()
        self.assertEqual(runner.device_name, "OpenVINO GPU")

    def test_openvino_auto_resolves_execution_device(self) -> None:
        runner = OpenVINOModelRunner.__new__(OpenVINOModelRunner)
        runner.compiled_device = "AUTO"
        runner.compiled_model = MagicMock()
        runner.compiled_model.get_property.return_value = ["GPU.0"]
        self.assertEqual(runner.device_name, "OpenVINO GPU.0")
        runner.compiled_model.get_property.side_effect = RuntimeError("no prop")
        self.assertEqual(runner.device_name, "OpenVINO AUTO")


class TestLoadRegistry(unittest.TestCase):
    def setUp(self) -> None:
        loaded_devices.clear()

    def test_get_optimized_runner_records_cpu_device(self) -> None:
        session = MagicMock()
        session.get_providers.return_value = ["CPUExecutionProvider"]

        with (
            patch.object(
                detection_runners,
                "get_ort_providers",
                return_value=(["CPUExecutionProvider"], [{}]),
            ),
            patch.object(
                detection_runners,
                "is_openvino_gpu_npu_available",
                return_value=False,
            ),
            patch.object(
                detection_runners.ort, "InferenceSession", return_value=session
            ),
        ):
            runner = get_optimized_runner(
                r"C:\Frigate\models\arcface.onnx", "CPU", "arcface"
            )

        self.assertIsInstance(runner, ONNXModelRunner)
        self.assertEqual(
            loaded_devices[r"C:\Frigate\models\arcface.onnx"],
            ("arcface", "CPU"),
        )


class TestLoadedDeviceSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        loaded_devices.clear()

    def test_record_and_snapshot_copy(self) -> None:
        record_loaded_device(r"C:\models\a.onnx", "arcface", "DirectML")
        snapshot = snapshot_loaded_devices()

        record_loaded_device(r"C:\models\b.onnx", "jina_v1", "CPU")

        self.assertEqual(
            snapshot,
            {r"C:\models\a.onnx": ("arcface", "DirectML")},
        )
        self.assertNotIn(r"C:\models\b.onnx", snapshot)

    def test_snapshot_survives_concurrent_inserts(self) -> None:
        stop = threading.Event()

        def writer() -> None:
            index = 0
            while not stop.is_set():
                record_loaded_device(rf"C:\models\{index}.onnx", "paddleocr", "CPU")
                index += 1

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                list(snapshot_loaded_devices().values())
        finally:
            stop.set()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
