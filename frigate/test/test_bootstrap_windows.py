"""Tests for the native Windows bootstrap script.

The script is standard library only so these can run anywhere; they exercise
the digest bookkeeping, archive extraction, layout resolution and launcher
generation without touching the network.
"""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_windows", REPO_ROOT / "bootstrap_windows.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap()


class TestBaseLayout(unittest.TestCase):
    def test_defaults_to_the_local_app_data_directory(self):
        with tempfile.TemporaryDirectory() as appdata:
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": appdata,
                    "FRIGATE_BASE_DIR": "",
                    "FRIGATE_CONFIG_DIR": "",
                    "FRIGATE_MEDIA_DIR": "",
                    "FRIGATE_CACHE_DIR": "",
                    "FRIGATE_TMP_DIR": "",
                },
            ):
                layout = bootstrap.BaseLayout()

        self.assertEqual(layout.base_dir, Path(appdata) / "Frigate")
        self.assertEqual(layout.config_dir, layout.base_dir / "config")
        self.assertEqual(layout.tmp_dir, layout.base_dir / "tmp")
        self.assertEqual(
            layout.restart_marker(), layout.base_dir / "tmp" / "restart.requested"
        )

    def test_base_dir_moves_every_directory(self):
        with tempfile.TemporaryDirectory() as base:
            with patch.dict(
                os.environ,
                {"FRIGATE_BASE_DIR": base, "LOCALAPPDATA": "C:\\unused"},
            ):
                layout = bootstrap.BaseLayout()

        self.assertEqual(layout.base_dir, Path(base))
        self.assertEqual(layout.config_dir, Path(base) / "config")

    def test_individual_directories_can_be_overridden(self):
        with (
            tempfile.TemporaryDirectory() as base,
            tempfile.TemporaryDirectory() as config,
        ):
            with patch.dict(
                os.environ,
                {"FRIGATE_BASE_DIR": base, "FRIGATE_CONFIG_DIR": config},
            ):
                layout = bootstrap.BaseLayout()

        self.assertEqual(layout.config_dir, Path(config))
        self.assertEqual(layout.media_dir, Path(base) / "media")


class TestDigests(unittest.TestCase):
    def _artifact(self, expected: str = ""):
        return bootstrap.PinnedArtifact(
            name="test",
            version="1.0",
            url="https://example.com/test.zip",
            members=("test.exe",),
            expected_sha256=expected,
        )

    def _archive(self, directory: Path, content: bytes = b"payload") -> Path:
        path = directory / "test-1.0.zip"

        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("test.exe", content)

        return path

    def test_first_download_records_its_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            store = Path(tmp) / "digests.json"

            digest = bootstrap.verify_or_record_digest(archive, self._artifact(), store)

            self.assertEqual(json.loads(store.read_text())["test"], digest)

    def test_second_download_must_match_the_recorded_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            store = Path(tmp) / "digests.json"
            digest = bootstrap.verify_or_record_digest(archive, self._artifact(), store)

            # same content verifies, changed content is rejected
            self.assertEqual(
                bootstrap.verify_or_record_digest(archive, self._artifact(), store),
                digest,
            )

            archive.write_bytes(b"tampered")

            with self.assertRaises(bootstrap.DigestMismatchError):
                bootstrap.verify_or_record_digest(archive, self._artifact(), store)

    def test_pinned_digest_is_enforced_from_the_first_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            store = Path(tmp) / "digests.json"

            with self.assertRaises(bootstrap.DigestMismatchError):
                bootstrap.verify_or_record_digest(
                    archive, self._artifact("0" * 64), store
                )

            digest = bootstrap.sha256_file(archive)

            self.assertEqual(
                bootstrap.verify_or_record_digest(
                    archive, self._artifact(digest), store
                ),
                digest,
            )
            # a pinned digest is not recorded to the store
            self.assertFalse(store.exists())

    def test_a_corrupt_digest_store_behaves_like_no_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(Path(tmp))
            store = Path(tmp) / "digests.json"
            store.write_text("{not json")

            digest = bootstrap.verify_or_record_digest(archive, self._artifact(), store)

            self.assertEqual(json.loads(store.read_text())["test"], digest)


class TestExtraction(unittest.TestCase):
    def test_only_the_named_members_are_extracted_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "build.zip"

            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("nested/deep/ffmpeg.exe", b"ffmpeg")
                bundle.writestr("nested/deep/ffprobe.exe", b"ffprobe")
                bundle.writestr("nested/deep/unrelated.txt", b"nope")

            dest = Path(tmp) / "bin"
            extracted = bootstrap.extract_members(
                archive,
                ("nested/deep/ffmpeg.exe", "nested/deep/ffprobe.exe"),
                dest,
            )

            self.assertEqual(
                sorted(path.name for path in extracted),
                ["ffmpeg.exe", "ffprobe.exe"],
            )
            self.assertEqual((dest / "ffmpeg.exe").read_bytes(), b"ffmpeg")
            self.assertFalse((dest / "unrelated.txt").exists())

    def test_a_missing_member_is_an_error_naming_the_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "build.zip"

            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("somethingelse.exe", b"x")

            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap.extract_members(archive, ("bin/ffmpeg.exe",), Path(tmp) / "b")

            self.assertIn("bin/ffmpeg.exe", str(raised.exception))


class TestLauncher(unittest.TestCase):
    def test_launcher_restarts_only_when_the_marker_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / ".venv"
            script = bootstrap.build_launcher_script(venv, bootstrap.BaseLayout())

        self.assertIn("while ($true)", script)
        self.assertIn("restart.requested", script)
        self.assertIn(str(bootstrap.python_executable_in(venv).resolve()), script)
        self.assertIn("-m frigate", script)
        # the marker is consumed so a later manual stop is not mistaken
        # for another restart request
        self.assertIn("Remove-Item $marker", script)

    def test_launcher_uses_the_given_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv3"
            script = bootstrap.build_launcher_script(venv, bootstrap.BaseLayout())

        self.assertIn(str(venv), script)


class TestInstallArtifact(unittest.TestCase):
    def test_existing_binaries_short_circuit_the_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = bootstrap.BaseLayout()
            config = Path(tmp) / "config"
            layout.config_dir = config
            (config / "ffmpeg").mkdir(parents=True)
            (config / "ffmpeg" / "ffmpeg.exe").write_bytes(b"existing")

            artifact = bootstrap.PinnedArtifact(
                name="ffmpeg",
                version="8.1.2",
                url="https://example.com/ffmpeg.zip",
                members=("build/ffmpeg.exe",),
                dest_subdir="ffmpeg",
            )

            def fail_download(url: str, dest: Path) -> None:
                raise AssertionError("a complete install must not download")

            with patch.object(bootstrap, "download", fail_download):
                installed = bootstrap.install_artifact(
                    artifact, layout, Path(tmp) / "downloads"
                )

            self.assertEqual(installed, [config / "ffmpeg" / "ffmpeg.exe"])


if __name__ == "__main__":
    unittest.main()


class TestServiceInstall(unittest.TestCase):
    """The SCM installer is driven with this environment's interpreter."""

    def test_the_environment_interpreter_is_passed_to_the_installer(self):
        python = Path("C:/Frigate/.venv/Scripts/python.exe")

        with patch.object(bootstrap.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            bootstrap.install_service(python)

        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell")
        self.assertIn("-Action", command)
        self.assertIn("Install", command)
        # without this the installer would guess, and could register a service
        # that runs an interpreter without Frigate or pywin32 in it
        self.assertIn(str(python), command)

    def test_a_refused_install_is_reported(self):
        """The installer refuses rather than registering a broken service."""
        with patch.object(bootstrap.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1)

            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap.install_service(Path("C:/Frigate/.venv/Scripts/python.exe"))

        self.assertIn("not installed", str(raised.exception))

    def test_a_missing_installer_is_reported(self):
        with patch.object(bootstrap, "REPO_ROOT", Path("C:/nowhere")):
            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap.install_service(Path("python.exe"))

        self.assertIn("not found", str(raised.exception))
