#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 @paxx12, @liberodark

set -e

cd "$(dirname "$0")/.."

cd docs

echo ">> Installing dependencies..."
bundle install

echo ">> Building Jekyll site..."
bundle exec jekyll build --destination ../_site

echo "[+] Build complete! Site generated in ../_site/"
