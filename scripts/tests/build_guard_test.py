#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

"""Regression tests for staged firmware startup safeguards."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SHEBANG_CHECK = ROOT / "scripts/helpers/check_shebangs.sh"
BUILD_ENVIRONMENT_CHECK = ROOT / "scripts/helpers/check_build_environment.sh"
CAMERA_HOOK = ROOT / (
    "overlays/firmware-extended/60-app-camera/root/etc/hooks/"
    "lmd.d/20-camera-selection.sh"
)


class ShebangGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u1-shebang-test-")
        self.addCleanup(self.temp.cleanup)
        self.rootfs = Path(self.temp.name) / "rootfs"
        self.rootfs.mkdir()

    def run_check(self):
        return subprocess.run(
            ["bash", str(SHEBANG_CHECK), str(self.rootfs)],
            capture_output=True, text=True, timeout=10,
        )

    def test_lf_shebangs_and_crlf_data_are_accepted(self):
        init_script = self.rootfs / "etc/init.d/S99example"
        init_script.parent.mkdir(parents=True)
        init_script.write_bytes(b"#!/bin/sh\nexec true\n")
        helper = self.rootfs / "usr/local/bin/helper.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b"#!/usr/bin/env python3\npass\n")
        (self.rootfs / "etc/settings.json").write_bytes(
            b'{"value": true}\r\n'
        )

        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_crlf_shebang_is_rejected_with_path(self):
        script = self.rootfs / "etc/init.d/S10broken"
        script.parent.mkdir(parents=True)
        script.write_bytes(b"#!/bin/sh\r\necho broken\r\n")

        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CRLF shebang", result.stderr)
        self.assertIn("S10broken", result.stderr)

    def test_non_shebang_crlf_file_is_not_rejected(self):
        config = self.rootfs / "etc/example.conf"
        config.parent.mkdir(parents=True)
        config.write_bytes(b"option=value\r\n")

        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class BuildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u1-build-env-test-")
        self.addCleanup(self.temp.cleanup)
        self.temp_dir = Path(self.temp.name)
        self.mock_bin = self.temp_dir / "bin"
        self.mock_bin.mkdir()
        self.findmnt = self.mock_bin / "findmnt"
        self.findmnt.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$MOCK_MOUNT_TYPE"\n',
            encoding="utf-8",
        )
        self.findmnt.chmod(0o755)

    def run_check(self, mount_type):
        env = os.environ.copy()
        env["WSL_DISTRO_NAME"] = "Ubuntu"
        env["MOCK_MOUNT_TYPE"] = mount_type
        env["PATH"] = str(self.mock_bin) + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            ["bash", str(BUILD_ENVIRONMENT_CHECK), str(self.temp_dir)],
            capture_output=True, text=True, timeout=10, env=env,
        )

    def test_windows_mounted_wsl_filesystems_are_rejected(self):
        for mount_type in ("drvfs", "9p"):
            with self.subTest(mount_type=mount_type):
                result = self.run_check(mount_type)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Windows-mounted filesystem", result.stderr)
                self.assertIn("~/src", result.stderr)

    def test_linux_filesystem_is_allowed(self):
        result = self.run_check("ext4")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CameraHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u1-camera-hook-test-")
        self.addCleanup(self.temp.cleanup)
        self.helper = Path(self.temp.name) / "extended-config"
        self.helper.write_text(
            "\n".join((
                "#!/bin/sh",
                'case "$CAMERA_RESULT" in',
                "  failure) exit 1 ;;",
                "  empty) exit 0 ;;",
                '  *) printf "%s" "$CAMERA_RESULT" ;;',
                "esac",
                "",
            )),
            encoding="utf-8",
        )
        self.helper.chmod(0o755)

    def run_hook(self, result, preload=""):
        env = os.environ.copy()
        env.pop("LD_PRELOAD", None)
        env["CAMERA_HOOK"] = str(CAMERA_HOOK)
        env["EXTENDED_CONFIG_BIN"] = str(self.helper)
        env["CAMERA_RESULT"] = result
        command = (
            'set -u; LD_PRELOAD="' + preload + '"; '
            'source "$CAMERA_HOOK" start; '
            'printf "%s|%s|%s" AFTER_HOOK "$CAMERA_INTERNAL" "$LD_PRELOAD"'
        )
        return subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=10, env=env,
        )

    def test_paxx12_keeps_imposter_mode_and_prior_preload(self):
        result = self.run_hook("paxx12", "previous.so")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AFTER_HOOK|paxx12|", result.stdout)
        self.assertIn(
            "/usr/local/lib/libv4l2-imposter.so:previous.so", result.stdout
        )

    def test_snapmaker_selection_keeps_normal_lmd_start(self):
        result = self.run_hook("snapmaker")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AFTER_HOOK|snapmaker|", result.stdout)
        self.assertNotIn("libv4l2-imposter.so", result.stdout)

    def test_explicit_none_still_stops_lmd_start(self):
        result = self.run_hook("none")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Internal camera is disabled", result.stdout)
        self.assertNotIn("AFTER_HOOK", result.stdout)

    def test_failed_helper_falls_back_to_snapmaker(self):
        result = self.run_hook("failure")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Could not read internal camera setting", result.stdout)
        self.assertIn("AFTER_HOOK|snapmaker|", result.stdout)

    def test_empty_or_unknown_setting_falls_back_to_snapmaker(self):
        for value in ("empty", "unexpected"):
            with self.subTest(value=value):
                result = self.run_hook(value)
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr
                )
                self.assertIn("using Snapmaker camera", result.stdout)
                self.assertIn("AFTER_HOOK|snapmaker|", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
