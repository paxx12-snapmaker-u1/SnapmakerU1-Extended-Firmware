# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 @paxx12, @horzadome

FROM debian:trixie

# The ccache is installed separately
# as otherwise not all symlinks are created
RUN apt-get update -y && \
    apt-get install -y \
      build-essential \
      cmake \
      pkg-config \
      squashfs-tools \
      git-core \
      bc \
      flex \
      bison \
      libssl-dev \
      dos2unix \
      sudo \
      sshpass \
      unzip \
      wget \
      g++-aarch64-linux-gnu \
      gcc-aarch64-linux-gnu \
      file \
      golang-go \
      ffmpeg \
      u-boot-tools \
    && apt-get -y install ccache \
    && git config --global --add safe.directory '*' \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/lib/ccache:${PATH}"
ENV CCACHE_MAXSIZE="512M"

RUN dpkg --add-architecture arm64 && \
    apt-get update -y && \
    apt-get install -y \
      libssl-dev:arm64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
