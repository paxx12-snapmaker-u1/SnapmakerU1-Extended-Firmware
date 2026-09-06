# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

upfile: upfile.c helpers.h
	$(CC) -o upfile upfile.c -lcrypto

test: upfile
	rm -rf tmp
	mkdir -p tmp
	./upfile unpack $(FIRMWARE_FILE) tmp
	./upfile pack tmp/update.bin tmp
	md5sum $(FIRMWARE_FILE) tmp/update.bin
	cmp $(FIRMWARE_FILE) tmp/update.bin
