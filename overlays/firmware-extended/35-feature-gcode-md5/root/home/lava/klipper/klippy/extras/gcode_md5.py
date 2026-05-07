## MD5 checking support for gcode
##
## Ported from DrA1ex/ff5m (https://github.com/DrA1ex/ff5m) for the
## Snapmaker U1 running paxx12 Extended Firmware.
##
## Changes from upstream:
##   - Removed dependency on mod_params (ff5m-specific)
##
## This file may be distributed under the terms of the GNU GPLv3 license

import hashlib
import os


class MD5Checker:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        self.delete_invalid_files = config.getboolean("delete_invalid", True)

        self.gcode.register_command("CHECK_MD5", self.cmd_CHECK_MD5,
                                    desc=self.cmd_CHECK_MD5_help)

        self.printer.register_event_handler("klippy:ready", self._init)

    def _init(self):
        self.vcard = self.printer.lookup_object("virtual_sdcard")

    def calculate_md5(self, file_path):
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            f.readline()  # Skip the first line (the "; MD5:..." header)
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)

        return hash_md5.hexdigest()

    def check_md5(self, file_path, delete):
        self.gcode.respond_raw("INFO: Begin MD5 check...")

        if os.path.isdir(file_path) or not os.path.exists(file_path):
            self.gcode.respond_raw(f"!! ERROR: File doesn't exist: {file_path}")
            return False

        with open(file_path, 'r') as f:
            first_line = f.readline().strip()
            if not first_line.startswith("; MD5:"):
                self.gcode.respond_raw("WARNING: No MD5 checksum found in G-code.")
                return True

            expected_md5 = first_line.split(':')[1]
            calculated_md5 = self.calculate_md5(file_path)

            if expected_md5 != calculated_md5:
                self.gcode.respond_raw(f"!! ERROR: MD5 checksum mismatch: {expected_md5} != {calculated_md5}")
                if delete and os.path.exists(file_path):
                    self.gcode.respond_raw(f"INFO: File {file_path} deleted!")
                    os.remove(file_path)

                    bmp_file_path = file_path.rsplit('.', maxsplit=1)[0] + ".bmp"
                    if os.path.exists(bmp_file_path):
                        os.remove(bmp_file_path)

                self.gcode.run_script_from_command('CANCEL_PRINT')
                return False

        self.gcode.respond_raw("INFO: MD5 checksum correct!")
        return True

    cmd_CHECK_MD5_help = (
        "Verify the MD5 checksum of a g-code file. "
        "Usage: CHECK_MD5 [FILENAME=/path/to/file.gcode] [DELETE=True|False]"
    )

    def cmd_CHECK_MD5(self, gcmd):
        filename = gcmd.get("FILENAME", None)
        delete = gcmd.get("DELETE", str(self.delete_invalid_files)) == "True"

        if not filename:
            filename = self.vcard.file_path() or ""

        if not self.check_md5(filename, delete):
            raise gcmd.error("MD5 check failed. Print cancelled!")


def load_config(config):
    return MD5Checker(config)
