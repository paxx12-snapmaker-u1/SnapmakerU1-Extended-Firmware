#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

set -e

# trixie cleanup
apt-get autopurge -y \
  build-essential \
  cpp-14-aarch64-linux-gnu \
  gcc-14-aarch64-linux-gnu \
  g++-14-aarch64-linux-gnu \
  binutils-aarch64-linux-gnu \
  libgcc-14-dev \
  vim-runtime \
  git

apt-get autopurge -y perl libperl*
apt-get autopurge -y python3-setuptools
apt-get autopurge -y packagekit libgstreamer*

# bookworm cleanup
apt-get autoremove -y libpython3.11-dev

# remove python bytecode files
find /usr -name '*.pyc' -delete
find /opt -name '*.pyc' -delete

# risky cleanup (breaks the system dependencies)
rm -rf /usr/include/*
rm -rf /usr/share/doc
rm -rf /usr/share/doc-base
rm -rf /usr/share/man

# general cleanup
rm -rf /var/cache/apt
rm -rf /var/lib/apt/lists
find "/var/log" -type f -delete
rm -rf "/tmp/"*
rm -rf /usr/share/locale
rm -rf /etc/machine-id
