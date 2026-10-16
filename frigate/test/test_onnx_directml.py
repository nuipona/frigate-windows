"""Tests for DirectML inference through the onnx detector.

onnxruntime-directml is the one Windows GPU runtime that serves AMD, Intel
and Nvidia from a single package, so it is the recommended detector for a
Windows machine without an Intel iGPU. Frigate does not depend on it: the
standard ONNX Runtime paths must keep working when the DirectML provider is
absent, and the provider must flow through to the session untouched when the
directml package is installed in its place.
"""

import unittest
from unittest.mock import MagicMock, patch

from frigate.detectors import detection_runners
from frigate.util import model as model_util
from frigate.util.model import get_ort_providers


class TestGetOrtProvidersDirectML(unittest.TestCase):
    def _providers(self, available: list[str], device: str = "AUTO"):
        with patch.object(
            model_util.ort,
            "get_available_providers",
            return_value=available,
        ):
            return get_ort_providers(False, device)

    def test_directml_provider_is_passed_through(self):
        providers, options = self._providers(
            ["DmlExecutionProvider", "CPUExecutionProvider"]
        )

        self.assertEqual(providers, ["DmlExecutionProvider", "CPUExecutionProvider"])
        # DirectML receives the selected adapter index; CPU keeps its arena setting.
        self.assertEqual(
            options,
            [{"device_id": 0}, {"enable_cpu_mem_arena": False}],
        )

    def test_directml_is_used_automatically_when_available(self):
        """A Windows install that swapped in onnxruntime-directml gets it by default."""
        providers, _ = self._providers(["DmlExecutionProvider", "CPUExecutionProvider"])

        self.assertEqual(providers[0], "DmlExecutionProvider")

    def test_cpu_runtime_without_directml_is_unchanged(self):
        providers, options = self._providers(
            ["AzureExecutionProvider", "CPUExecutionProvider"]
        )

        # Azure is skipped so a CPU-only install stays on CPU
        self.assertEqual(providers, ["CPUExecutionProvider"])
        self.assertEqual(options, [{"enable_cpu_mem_arena": False}])


class TestRunnerWithDirectML(unittest.TestCase):
    def _runner(self, session: MagicMock):
        with (
            patch.object(
                detection_runners,
                "get_ort_providers",
                return_value=(
                    ["DmlExecutionProvider", "CPUExecutionProvider"],
                    [{}, {}],
                ),
            ),
            patch.object(
                detection_runners, "is_openvino_gpu_npu_available", return_value=False
            ),
            patch.object(
                detection_runners.ort, "InferenceSession", return_value=session
            ) as session_factory,
            patch.object(
                detection_runners, "get_ort_session_options", return_value=None
            ),
        ):
            runner = detection_runners.get_optimized_runner(
                "/models/yolo.onnx", "AUTO", "yolonas"
            )

        return runner, session_factory

    def test_session_receives_the_directml_provider(self):
        session = MagicMock()
        session.get_inputs.return_value = [MagicMock()]
        session.get_outputs.return_value = [MagicMock()]
        session.get_providers.return_value = [
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ]

        runner, session_factory = self._runner(session)

        _, kwargs = session_factory.call_args
        self.assertEqual(
            kwargs["providers"],
            ["DmlExecutionProvider", "CPUExecutionProvider"],
        )
        self.assertEqual(runner.device_name, "DirectML")

    def test_directml_reports_as_directml(self):
        session = MagicMock()
        session.get_inputs.return_value = [MagicMock()]
        session.get_outputs.return_value = [MagicMock()]
        session.get_providers.return_value = [
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ]

        runner = detection_runners.ONNXModelRunner.__new__(
            detection_runners.ONNXModelRunner
        )
        runner.ort = session

        self.assertEqual(runner.device_name, "DirectML")


if __name__ == "__main__":
    unittest.main()
