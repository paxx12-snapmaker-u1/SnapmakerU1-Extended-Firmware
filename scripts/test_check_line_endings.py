from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from scripts.check_line_endings import check_rootfs


class RootfsLineEndingTests(unittest.TestCase):
    def run_check(self, files: dict[str, bytes]) -> int:
        with tempfile.TemporaryDirectory() as directory:
            rootfs = Path(directory)
            for relative_path, data in files.items():
                path = rootfs / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return check_rootfs(rootfs)

    def test_accepts_lf_scripts(self) -> None:
        self.assertEqual(self.run_check({"etc/init.d/S99good": b"#!/bin/sh\necho good\n"}), 0)

    def test_rejects_crlf_init_script(self) -> None:
        self.assertEqual(self.run_check({"etc/init.d/S99bad": b"#!/bin/sh\r\necho bad\r\n"}), 1)

    def test_rejects_crlf_shebang_script_outside_init(self) -> None:
        self.assertEqual(self.run_check({"usr/local/bin/bad": b"#!/bin/sh\r\necho bad\r\n"}), 1)

    def test_ignores_unrelated_crlf_data(self) -> None:
        self.assertEqual(self.run_check({"usr/share/data.txt": b"not a script\r\n"}), 0)


if __name__ == "__main__":
    unittest.main()
