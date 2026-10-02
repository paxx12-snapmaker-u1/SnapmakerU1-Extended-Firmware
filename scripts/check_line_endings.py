#!/usr/bin/env python3
"""Reject carriage returns in firmware source and packaged Linux scripts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent


def git_files(directory: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=directory,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [directory / os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def source_files() -> list[tuple[Path, bool]]:
    files = [(path, False) for path in git_files(REPO_ROOT)]
    result = subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    for line in result.stdout.splitlines():
        if not line or line[0] == "-":
            continue
        fields = line[1:].strip().split(maxsplit=2)
        if len(fields) < 2:
            continue
        submodule = REPO_ROOT / fields[1]
        files.extend((path, True) for path in git_files(submodule))
    return files


def is_utf8_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def is_firmware_script(path: Path, data: bytes) -> bool:
    relative = path.relative_to(REPO_ROOT)
    if data.startswith(b"#!") or path.name == "Makefile" or path.suffix in {".mk", ".sh"}:
        return True
    if relative.parts[0] != "overlays":
        return False
    if "scripts" in relative.parts or "pre-scripts" in relative.parts:
        return True
    try:
        root_index = relative.parts.index("root")
    except ValueError:
        return data.startswith(b"#!")
    root_path = relative.parts[root_index + 1 :]
    return root_path[:2] == ("etc", "init.d") or data.startswith(b"#!")


def check_source() -> int:
    bad: list[Path] = []
    checked = 0
    for path, scan_all_text in source_files():
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if not is_utf8_text(data) or (not scan_all_text and not is_firmware_script(path, data)):
            continue
        checked += 1
        if b"\r" in data:
            bad.append(path.relative_to(REPO_ROOT))

    if bad:
        print("Error: carriage returns found in firmware script sources:", file=sys.stderr)
        for path in bad:
            print(f"  {path.as_posix()}", file=sys.stderr)
        print("Normalize these files to LF before building.", file=sys.stderr)
        print("On Windows, set core.autocrlf=false in this repository and its submodules.", file=sys.stderr)
        return 1

    print(f"Line-ending check passed for {checked} firmware script sources.")
    return 0


def rootfs_script_files(rootfs: Path) -> list[Path]:
    candidates: set[Path] = set()
    init_dir = rootfs / "etc" / "init.d"
    if init_dir.is_dir():
        candidates.update(path for path in init_dir.iterdir() if path.is_file() and not path.is_symlink())

    for directory, dirnames, filenames in os.walk(rootfs, followlinks=False):
        current = Path(directory)
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with path.open("rb") as handle:
                    if handle.read(2) == b"#!":
                        candidates.add(path)
            except OSError as error:
                print(f"Error reading {path}: {error}", file=sys.stderr)
                raise
    return sorted(candidates)


def check_rootfs(rootfs: Path) -> int:
    if not rootfs.is_dir():
        print(f"Error: root filesystem directory does not exist: {rootfs}", file=sys.stderr)
        return 2

    scripts = rootfs_script_files(rootfs)
    bad = [path.relative_to(rootfs) for path in scripts if b"\r" in path.read_bytes()]
    if bad:
        print("Error: carriage returns found in packaged Linux scripts:", file=sys.stderr)
        for path in bad:
            print(f"  /{path.as_posix()}", file=sys.stderr)
        print("Refusing to package a root filesystem with invalid Linux script line endings.", file=sys.stderr)
        return 1

    print(f"Line-ending check passed for {len(scripts)} packaged Linux scripts.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", type=Path, help="check init and shebang scripts in an assembled root filesystem")
    args = parser.parse_args()
    return check_rootfs(args.rootfs.resolve()) if args.rootfs else check_source()


if __name__ == "__main__":
    raise SystemExit(main())
