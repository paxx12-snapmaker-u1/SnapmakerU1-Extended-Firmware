#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

import configparser
import logging
import logging.handlers
import os
import runpy
import sys


LOG_FILE = "/oem/printer_data/logs/openrfid.log"
MAX_ROTATIONS = 7


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <target.cfg> [source.cfg ...]")
        sys.exit(1)

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    syslogHandler = logging.handlers.SysLogHandler(address="/dev/log")
    stderrHandler = logging.StreamHandler(sys.stderr)
    fileHandler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=MAX_ROTATIONS,
    )

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for handler in (syslogHandler, stderrHandler, fileHandler):
        handler.setFormatter(formatter)

    logging.root.handlers = [syslogHandler, stderrHandler, fileHandler]
    logging.root.setLevel(logging.DEBUG)

    target = sys.argv[1]
    sources = sys.argv[2:]

    config = configparser.RawConfigParser()
    config.optionxform = str
    config.read(sources)

    with open(target, "w") as f:
        config.write(f)

    sys.argv = [sys.argv[0], target]
    sys.path.insert(0, "/usr/local/share/openrfid")
    os.chdir("/usr/local/share/openrfid")
    runpy.run_path("main.py", run_name="__main__")


if __name__ == "__main__":
    main()
