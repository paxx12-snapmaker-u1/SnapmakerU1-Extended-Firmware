#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

"""Exercise verified downloads and Makefile cache reuse without real firmware."""

import functools
import hashlib
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/helpers/cache_file.sh"
PAYLOAD = b"verified build dependency\n"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@contextmanager
def serve(directory):
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class CacheFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u1-download-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "cache" / "payload.bin"
        self.served = self.root / "served"
        self.served.mkdir()
        (self.served / "payload.bin").write_bytes(PAYLOAD)

    def run_helper(self, url, expected=SHA256, extract=None, env=None):
        args = ["bash", str(HELPER), str(self.target), url, expected]
        if extract is not None:
            args.append(str(extract))
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=15, env=env)
        self.assertEqual(list(self.target.parent.glob("*.tmp.*")), [])
        return result

    def test_valid_cache_skips_download(self):
        self.target.parent.mkdir()
        self.target.write_bytes(PAYLOAD)
        # A forbidden wget stub proves a cache hit never attempts a download.
        env = self.wget_stub('echo "unexpected download" >&2; exit 99\n')
        result = self.run_helper("https://unused.invalid/payload.bin", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_verified_download_is_published(self):
        with serve(self.served) as url:
            result = self.run_helper(url + "/payload.bin")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)

    def test_corrupt_cache_is_replaced(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"corrupt cache")
        with serve(self.served) as url:
            result = self.run_helper(url + "/payload.bin")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_checksum_failure_preserves_destination(self):
        for existing in (None, b"previous cached bytes"):
            with self.subTest(existing=existing):
                self.target.parent.mkdir(exist_ok=True)
                if existing is None:
                    self.target.unlink(missing_ok=True)
                else:
                    self.target.write_bytes(existing)
                with serve(self.served) as url:
                    result = self.run_helper(url + "/payload.bin", "0" * 64)
                self.assertNotEqual(result.returncode, 0)
                if existing is None:
                    self.assertFalse(self.target.exists())
                else:
                    self.assertEqual(self.target.read_bytes(), existing)

    def wget_stub(self, body):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "wget"
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        return env

    def test_partial_download_failure_preserves_destination(self):
        env = self.wget_stub('printf partial > "$2"\nexit 8\n')
        for existing in (None, b"previous cached bytes"):
            with self.subTest(existing=existing):
                self.target.parent.mkdir(exist_ok=True)
                if existing is None:
                    self.target.unlink(missing_ok=True)
                else:
                    self.target.write_bytes(existing)
                result = self.run_helper("https://unused.invalid/payload.bin", env=env)
                self.assertNotEqual(result.returncode, 0)
                if existing is None:
                    self.assertFalse(self.target.exists())
                else:
                    self.assertEqual(self.target.read_bytes(), existing)

    def test_http_failure_leaves_no_destination(self):
        with serve(self.served) as url:
            result = self.run_helper(url + "/missing.bin")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())

    def test_verified_archive_still_extracts(self):
        archive = self.served / "payload.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(self.served / "payload.bin", arcname="payload.bin")
        self.target = self.target.with_name("payload.tar.gz")
        expected = hashlib.sha256(archive.read_bytes()).hexdigest()
        extract = self.root / "extracted"
        with serve(self.served) as url:
            result = self.run_helper(url + "/payload.tar.gz", expected, extract)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((extract / "payload.bin").read_bytes(), PAYLOAD)

    def make_fixture(self, download=PAYLOAD):
        (self.root / "Makefile").write_text(
            (ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "vars.mk").write_text(
            f"FIRMWARE_FILE=stock.bin\nFIRMWARE_SHA256={SHA256}\n", encoding="utf-8")
        helper = self.root / "scripts/helpers/cache_file.sh"
        helper.parent.mkdir(parents=True)
        shutil.copyfile(HELPER, helper)
        fixture = self.root / "download.bin"
        fixture.write_bytes(download)
        env = self.wget_stub('printf called >> "$DOWNLOAD_LOG"\ncp "$DOWNLOAD_FIXTURE" "$2"\n')
        env["DOWNLOAD_LOG"] = str(self.root / "downloads.log")
        env["DOWNLOAD_FIXTURE"] = str(fixture)
        return env

    def run_make(self, env):
        return subprocess.run(["make", "--no-print-directory", "firmware"],
                              cwd=self.root, env=env, capture_output=True,
                              text=True, timeout=15)

    def test_make_verifies_existing_stock_firmware(self):
        env = self.make_fixture()
        stock = self.root / "firmware/stock.bin"
        stock.parent.mkdir()
        stock.write_bytes(PAYLOAD)
        result = self.run_make(env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "downloads.log").exists())
        # A later invocation must check again, even though the file exists.
        stock.write_bytes(b"corrupted between builds")
        result = self.run_make(env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(stock.read_bytes(), PAYLOAD)
        self.assertEqual((self.root / "downloads.log").read_text(), "called")

    def test_make_checksum_failure_does_not_publish_stock_firmware(self):
        env = self.make_fixture(download=b"wrong firmware")
        result = self.run_make(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "firmware/stock.bin").exists())
        self.assertEqual(list((self.root / "firmware").glob("*.tmp.*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
