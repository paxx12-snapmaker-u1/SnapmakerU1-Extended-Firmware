# gcode_integrity_check.py
#
# Moonraker component that verifies gcode file integrity.
#
# The primary hook is job_state:started, which validates a file as soon as a print begins. If a check
# fails, the print is cancelled.
#
# An optional second hook on file_manager:filelist_changed can validate files at upload time and delete
# bad uploads before any print is attempted. It is disabled by default and can be enabled by uncommenting
# its registration in __init__.
#
# Checks (configurable):
#
#   End-of-file marker check (default: enabled)
#     Scans the last N executable lines for a configurable marker. Detects truncated uploads. Default
#     marker is "PRINT_END"; can be set to any string including a comment such as "; EXECUTABLE_BLOCK_END".
#
#   MD5 checksum check (default: enabled)
#     If the first line is "; MD5:<hash>", verifies the hash covers the rest of the file. Files without
#     a MD5 header are silently skipped.
#
# Both checks fail open: an unreadable file is allowed through. Only a positively detected failure
# triggers the error action.
#
# Configuration (extended/moonraker/gcode_integrity_check.cfg):
#
#   [gcode_integrity_check]
#   require_end_marker: True
#   end_marker: PRINT_END
#   executable_scan_lines: 25
#   enable_md5: True
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations

import hashlib
import logging
import os
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from moonraker.confighelper import ConfigHelper

GCODE_EXTENSIONS = (".gcode", ".g", ".gco")
MD5_HEADER = "; MD5:"
MD5_HEX_LEN = 32


class Md5Result(Enum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    NO_HEADER = "no_header"
    MALFORMED = "malformed"


def _sanitize_for_gcode(msg: str) -> str:
    """Strip characters Klipper's gcode parser cannot tolerate inside a quoted argument. Semicolons
    become commas since they are comment markers."""
    return msg.replace('"', "").replace("'", "").replace(";", ",")


class GcodeIntegrityCheck:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()

        self.require_end_marker: bool = config.getboolean("require_end_marker", True)
        self.end_marker: str = config.get("end_marker", "PRINT_END").strip()
        self.executable_scan_lines: int = config.getint("executable_scan_lines", 25)
        self.enable_md5: bool = config.getboolean("enable_md5", True)

        if self.require_end_marker and not self.end_marker:
            raise config.error(
                "[gcode_integrity_check]: 'end_marker' cannot be empty when 'require_end_marker' is True."
            )
        if self.executable_scan_lines < 1:
            raise config.error("[gcode_integrity_check]: 'executable_scan_lines' must be at least 1")

        # Primary hook: validate at print start, cancel on failure.
        self.server.register_event_handler("job_state:started", self._handle_job_started)

        # Optional hook: validate at upload, delete bad files. Disabled by default; uncomment to enable.
        # self.server.register_event_handler("file_manager:filelist_changed", self._handle_filelist_changed)

    # ----------------------------------------------------------------------------------------------------
    # Shared core: messaging, check runner, and check implementations.
    # ----------------------------------------------------------------------------------------------------

    def _resolve_gcode_path(self, filename: str) -> str:
        fm = self.server.lookup_component("file_manager")
        root_dir = fm.file_paths.get("gcodes", "")
        return os.path.join(root_dir, filename)

    def _send_console(self, prefix: str, msg: str) -> None:
        """Push a console message via the websocket, bypassing Klipper's gcode queue (which can be busy
        with homing at print start)."""
        try:
            safe_msg = _sanitize_for_gcode(msg)
            self.server.send_event("server:gcode_response", f"{prefix} integrity_check: {safe_msg}")
        except Exception as e:
            logging.warning(f"gcode_integrity_check: could not send console message: {e}")

    def _respond(self, msg: str) -> None:
        """Send an informational console message (// prefix)."""
        self._send_console("//", msg)

    def _alert(self, msg: str) -> None:
        """Send an error console message (!! prefix, renders as red in Fluidd/Mainsail)."""
        self._send_console("!!", msg)

    async def _run_checks(self, filepath: str) -> List[str]:
        """Run enabled checks and return a list of error messages. File I/O runs in a thread to avoid
        blocking the event loop."""
        errors: List[str] = []
        event_loop = self.server.get_event_loop()

        if self.require_end_marker:
            found = await event_loop.run_in_thread(self._check_end_marker, filepath)
            if not found:
                errors.append(
                    f"end marker [{self.end_marker}] not found within last {self.executable_scan_lines} "
                    f"executable lines - file may be truncated"
                )

        if self.enable_md5:
            md5_result = await event_loop.run_in_thread(self._check_md5, filepath)
            if md5_result == Md5Result.VERIFIED:
                self._respond("MD5 checksum verified.")
            elif md5_result == Md5Result.NO_HEADER:
                self._respond("No MD5 header found - skipping checksum verification.")
            elif md5_result == Md5Result.MALFORMED:
                self._alert("MD5 header present but malformed - skipping checksum verification.")
            elif md5_result == Md5Result.MISMATCH:
                errors.append("MD5 checksum mismatch - file may be corrupt")

        return errors

    def _read_lines_from_end(self, filepath: str, chunk_size: int = 8192):
        """Yield non-empty stripped lines in reverse file order. Memory usage is bounded by chunk_size
        plus the longest line."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        def _clean(raw: bytes) -> Optional[str]:
            text = raw.decode("utf-8", errors="replace").strip()
            return text or None

        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            remaining = f.tell()
            # Leftmost segment of the current buffer; may continue into an earlier (not-yet-read) chunk,
            # so it's held until proven complete.
            pending = b""

            while remaining > 0:
                read = min(chunk_size, remaining)
                remaining -= read
                f.seek(remaining)
                chunk = f.read(read)

                # \r and \n are single-byte in UTF-8 and cannot appear inside a multi-byte sequence, so
                # byte-level normalization is safe.
                buffer = (chunk + pending).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                parts = buffer.split(b"\n")

                pending = parts[0]
                for raw_line in reversed(parts[1:]):
                    if (line := _clean(raw_line)) is not None:
                        yield line

            if (line := _clean(pending)) is not None:
                yield line

    def _check_end_marker(self, filepath: str) -> bool:
        """Walk backwards looking for end_marker. Comment lines are matched but don't count toward the executable
        line budget, so a marker emitted between the file tail and the last command is still found."""
        try:
            executable_checked = 0
            for line in self._read_lines_from_end(filepath):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped == self.end_marker:
                    return True
                if stripped.startswith(";"):
                    continue
                executable_checked += 1
                if executable_checked >= self.executable_scan_lines:
                    return False
            return False
        except OSError as e:
            logging.warning(f"gcode_integrity_check: could not read {filepath}: {e}")
            return True  # fail open

    def _check_md5(self, filepath: str) -> Optional[Md5Result]:
        """Verify a '; MD5:<hash>' header against the rest of the file. The header is the first line of the
        file in the format '; MD5:<32-hex-char-hash>'.

        Returns a Md5Result on a header-present path, or None if the file is unreadable (fail open)."""
        try:
            with open(filepath, "rb") as f:
                first_line = f.readline().decode("utf-8", errors="ignore").strip()

                if not first_line.startswith(MD5_HEADER):
                    return Md5Result.NO_HEADER

                expected = first_line[len(MD5_HEADER):].strip().lower()
                if len(expected) != MD5_HEX_LEN:
                    logging.warning(f"gcode_integrity_check: malformed MD5 header in {filepath}: {first_line!r}")
                    return Md5Result.MALFORMED

                md5 = hashlib.md5()
                for chunk in iter(lambda: f.read(65536), b""):
                    md5.update(chunk)

            return Md5Result.VERIFIED if md5.hexdigest() == expected else Md5Result.MISMATCH
        except OSError as e:
            logging.warning(f"gcode_integrity_check: could not read {filepath}: {e}")
            return None  # fail open

    # ----------------------------------------------------------------------------------------------------
    # Primary handler: validate at print start.
    #
    # job_state:started fires within ~250 ms of the user clicking print, well before any motion.
    # CANCEL_PRINT itself enters the gcode queue and lands after Klipper finishes its current startup
    # sequence (homing, probing, etc).
    # ----------------------------------------------------------------------------------------------------

    async def _handle_job_started(self, prev_stats: dict, new_stats: dict) -> None:
        filename = new_stats.get("filename", "")
        if not filename:
            return

        filepath = self._resolve_gcode_path(filename)
        if not os.path.exists(filepath):
            logging.warning(f"gcode_integrity_check: file not found: {filepath}")
            return

        self._respond(f"Checking integrity of {filename}...")

        errors = await self._run_checks(filepath)

        if errors:
            reason = "; ".join(errors)
            logging.error(f"gcode_integrity_check: cancelling print - {reason}")
            self._alert(f"Integrity check FAILED: {reason}")

            safe_reason = _sanitize_for_gcode(reason)
            kapis = self.server.lookup_component("klippy_apis")
            await kapis.run_gcode(f'CANCEL_PRINT REASON="{safe_reason}"')
        else:
            self._respond("Integrity check passed.")

    # ----------------------------------------------------------------------------------------------------
    # Optional handler: validate at upload.
    #
    # Disabled by default. file_manager:filelist_changed fires after upload and metadata processing
    # complete, so the file is fully on disk. On failure the file is deleted; no print has been started.
    # ----------------------------------------------------------------------------------------------------

    async def _handle_filelist_changed(self, result: dict) -> None:
        action = result.get("action", "")
        item = result.get("item", {})
        if action != "create_file" or item.get("root", "") != "gcodes":
            return

        filename = item.get("path", "")
        if not filename.lower().endswith(GCODE_EXTENSIONS):
            return

        filepath = self._resolve_gcode_path(filename)
        self._respond(f"Checking integrity of uploaded file: {filename}")

        errors = await self._run_checks(filepath)
        if not errors:
            self._respond("Upload integrity check passed.")
            return

        reason = "; ".join(errors)
        logging.error(f"gcode_integrity_check: deleting bad upload {filename} - {reason}")

        deleted = self._delete_file(filepath)
        suffix = "File deleted." if deleted else "Automatic deletion FAILED - please remove the file manually."
        self._alert(f"Upload of {filename} FAILED integrity check: {reason}. {suffix}")

    def _delete_file(self, filepath: str) -> bool:
        try:
            os.remove(filepath)
            return True
        except OSError as e:
            logging.error(f"gcode_integrity_check: failed to delete {filepath}: {e}")
            return False


def load_component(config: ConfigHelper) -> GcodeIntegrityCheck:
    return GcodeIntegrityCheck(config)
