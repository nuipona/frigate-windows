"""Tests for Frigate+ model metadata."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from frigate.plus import PlusApi, load_plus_model_info


class TestPlusModelList(unittest.TestCase):
    def _get_models(self, models: list[dict]) -> list[dict]:
        api = PlusApi.__new__(PlusApi)
        response = MagicMock(ok=True, json=lambda: {"list": models})

        with patch.object(PlusApi, "_get", return_value=response):
            return api.get_models()["list"]

    def test_native_windows_detectors_are_unchanged(self):
        models = self._get_models([{"supportedDetectors": ["cpu", "openvino", "onnx"]}])

        self.assertEqual(models[0]["supportedDetectors"], ["cpu", "openvino", "onnx"])

    def test_unsupported_variants_and_models_are_removed(self):
        models = self._get_models(
            [
                {
                    "id": "mixed",
                    "supportedDetectors": ["hailo", "onnx", "rknn"],
                    "hailoDevice": "hailo8l",
                },
                {"id": "unsupported", "supportedDetectors": ["edgetpu"]},
            ]
        )

        self.assertEqual(models, [{"id": "mixed", "supportedDetectors": ["onnx"]}])

    def test_an_empty_list(self):
        self.assertEqual(self._get_models([]), [])


class TestLoadPlusModelInfo(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        patcher = patch("frigate.plus.MODEL_CACHE_DIR", self.cache.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, model_id: str, content: str) -> None:
        with open(os.path.join(self.cache.name, f"{model_id}.json"), "w") as file:
            file.write(content)

    def test_cached_metadata_is_loaded(self):
        metadata = {"supportedDetectors": ["cpu", "openvino"]}
        self._write("abc", json.dumps(metadata))

        self.assertEqual(load_plus_model_info("abc"), metadata)

    def test_a_missing_file(self):
        self.assertIsNone(load_plus_model_info("nope"))

    def test_an_unreadable_file(self):
        self._write("bad", "{not json")

        self.assertIsNone(load_plus_model_info("bad"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
