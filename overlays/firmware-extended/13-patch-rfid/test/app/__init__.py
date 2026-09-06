# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

import os
import pkgutil

BASE = os.path.dirname(os.path.abspath(__file__))

# Extend the module search path to include extras directories
__path__ = pkgutil.extend_path(__path__, __name__)
__path__.append(os.path.join(BASE, "../../root/home/lava/klipper/klippy/extras"))
__path__.append(os.path.join(BASE, "../../../../../tmp/extracted/rootfs/home/lava/klipper/klippy/extras"))
