# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

all: rk2918_tools upfile resource_tool

# ================= Tools =================

%: FORCE
	make -C $@

clean:
	rm -rf tmp

# ================= Test =================

test: test_repack_upfile test_repack_afptool test_repack_img

# re-pack using upfile tool

tmp/test/update.img: $(FIRMWARE_FILE) upfile
ifeq (,$(FIRMWARE_FILE))
	$(error FIRMWARE_FILE is not set)
endif
	# cleanup previous test data
	rm -rf tmp/test
	mkdir -p tmp/test
	./upfile/upfile unpack $< tmp/test

test_repack_upfile: $(FIRMWARE_FILE) tmp/test/update.img upfile
	./upfile/upfile pack tmp/test/update.bin tmp/test
	md5sum $< tmp/test/update.bin
	cmp $< tmp/test/update.bin

# re-pack using afptool from rk2918_tools

tmp/test/rk-unpacked/rootfs.img: tmp/test/rk-rom.img rk2918_tools
	mkdir -p tmp/test/rk-unpacked
	./rk2918_tools/afptool -unpack $< tmp/test/rk-unpacked

test_repack_afptool: tmp/test/rk-rom.img tmp/test/rk-unpacked/rootfs.img rk2918_tools
	./rk2918_tools/afptool -pack tmp/test/rk-unpacked tmp/test/rk-rom-repacked.img
	md5sum $< tmp/test/rk-rom-repacked.img
	cmp $< tmp/test/rk-rom-repacked.img

# re-pack using img from rk2918_tools

tmp/test/rk-rom.img: tmp/test/update.img rk2918_tools
	./rk2918_tools/img_unpack $< tmp/test/rk-loader.img tmp/test/rk-rom.img tmp/test/rk-meta.txt

test_repack_img: tmp/test/update.img tmp/test/rk-rom.img rk2918_tools
	./rk2918_tools/img_maker tmp/test/rk-loader.img tmp/test/rk-rom.img tmp/test/update-new.img tmp/test/rk-meta.txt
	md5sum $< tmp/test/update-new.img
	cmp $< tmp/test/update-new.img
