"""Tests for the Windows job object helper.

The job object itself only exists on Windows, so :func:`install` cannot be
exercised here. What *can* be checked everywhere is the part that silently
breaks: the ctypes layout of the structures handed to
``SetInformationJobObject``. If a field is the wrong width the call still
succeeds and quietly sets nothing, which is worse than an error.
"""

import ctypes
import os
import unittest
from unittest.mock import patch

from frigate.util import job_object
from frigate.util.platform import IS_WINDOWS


class TestJobObjectStructures(unittest.TestCase):
    """The structures have to match the Windows SDK layout byte for byte."""

    def test_basic_limit_information_layout(self):
        info = job_object._JobObjectBasicLimitInformation()
        expected = {
            "PerProcessUserTimeLimit": 0,
            "PerJobUserTimeLimit": 8,
            "LimitFlags": 16,
            # padding after the DWORD so the SIZE_T is aligned
            "MinimumWorkingSetSize": 24,
            "MaximumWorkingSetSize": 32,
            "ActiveProcessLimit": 40,
            # padding after that DWORD so the ULONG_PTR is aligned
            "Affinity": 48,
            "PriorityClass": 56,
            "SchedulingClass": 60,
        }

        for name, offset in expected.items():
            self.assertEqual(
                getattr(type(info), name).offset,
                offset,
                f"{name} is at the wrong offset",
            )

        self.assertEqual(ctypes.sizeof(info), 64)

    def test_extended_limit_information_layout(self):
        info = job_object._JobObjectExtendedLimitInformation()

        self.assertEqual(type(info).BasicLimitInformation.offset, 0)
        self.assertEqual(type(info).IoInfo.offset, 64)
        self.assertEqual(ctypes.sizeof(info), 144)

    def test_dword_is_four_bytes(self):
        """wintypes.DWORD is c_ulong, which is 8 bytes off Windows."""
        self.assertEqual(ctypes.sizeof(job_object._DWORD), 4)

    def test_kill_on_close_flag_fits_and_survives(self):
        info = job_object._JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = (
            job_object._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        self.assertEqual(info.BasicLimitInformation.LimitFlags, 0x2000)


class TestJobObjectInstall(unittest.TestCase):
    def setUp(self):
        job_object.reset()
        self.addCleanup(job_object.reset)

    @unittest.skipIf(
        IS_WINDOWS, "a Windows machine installs its own job object instead"
    )
    def test_is_a_noop_off_windows(self):
        self.assertFalse(job_object.install())
        self.assertFalse(job_object.installed())

    @unittest.skipUnless(IS_WINDOWS, "job objects only exist on Windows")
    def test_installs_its_own_job_object(self):
        """The point of the module: this process gets a job that outlives it."""
        installed = job_object.install()

        self.assertEqual(installed, job_object.installed())

        if not installed:
            # an environment can refuse the job (a service wrapper that already
            # put this process in a job with UI restrictions, for example), and
            # then the reason why is recorded rather than raised
            self.assertIsNotNone(job_object.error())

    @patch.object(job_object, "IS_WINDOWS", True)
    @patch.object(os, "name", "nt")
    def test_the_disable_variable_wins(self):
        for value in ("1", "true", "YES", " on "):
            job_object.reset()
            with patch.dict(os.environ, {job_object.DISABLE_VAR: value}):
                self.assertFalse(job_object.install())
            self.assertIsNotNone(job_object.error())

    @patch.object(job_object, "IS_WINDOWS", True)
    def test_installing_twice_only_acts_once(self):
        with patch.object(job_object, "_handle", 1234):
            self.assertTrue(job_object.install())


if __name__ == "__main__":
    unittest.main()
