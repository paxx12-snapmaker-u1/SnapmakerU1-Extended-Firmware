# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

# shellcheck shell=sh disable=SC1091,SC2039,SC2166
# Add /usr/local/bin to PATH if that directory exists

[ -d /usr/local/bin ] && export PATH="/usr/local/bin:$PATH"
