"""Tests for per-detector process statistics."""

import unittest
from unittest.mock import MagicMock

from frigate.stats.util import get_detector_stats


def _detector() -> MagicMock:
    detector = MagicMock()
    detector.avg_inference_speed.value = 0.01
    detector.detection_start.value = 2.5
    detector.detect_process.pid = 42
    return detector


class TestDetectorStats(unittest.TestCase):
    def test_native_detector_stats_include_speed_and_process(self):
        stats = get_detector_stats({"detectors": {"onnx:0": _detector()}})

        self.assertEqual(
            stats["onnx:0"],
            {"inference_speed": 10.0, "detection_start": 2.5, "pid": 42},
        )


if __name__ == "__main__":
    unittest.main()
