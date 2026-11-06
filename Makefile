# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12, @horzadome

include vars.mk

all: tools

# ================= Build Tools =================

OUTPUT_FILE := firmware/firmware.bin
BUILD_DIR ?= tmp/firmware

ifneq (,$(PROFILE))
PROFILE_PARTS := $(subst -, ,$(PROFILE))
FIRMWARE_NAME := $(firstword $(PROFILE_PARTS))
MOD_NAMES := $(wordlist 2,$(words $(PROFILE_PARTS)),$(PROFILE_PARTS))
OVERLAYS += $(wildcard overlays/common/*/)
OVERLAYS += $(wildcard overlays/firmware-$(FIRMWARE_NAME)/*/)
OVERLAYS += $(foreach p,$(MOD_NAMES),$(wildcard overlays/mods/$(p)/*/))
endif

FIRMWARES := $(patsubst overlays/firmware-%,%,$(wildcard overlays/firmware-*))
MOD_LIST := $(patsubst overlays/mods/%/,%,$(wildcard overlays/mods/*/))
INVALID_MOD_NAMES := $(filter-out $(MOD_LIST),$(MOD_NAMES))

$(OUTPUT_FILE): firmware/$(FIRMWARE_FILE) tools
ifeq (,$(PROFILE))
	@echo "Please specify a firmware using 'make PROFILE=<firmware>[-<mod>]*'. Available firmwares are: $(FIRMWARES). Available mods are: $(MOD_LIST)."
	@exit 1
else ifeq (,$(filter $(FIRMWARE_NAME),$(FIRMWARES)))
	@echo "Invalid firmware '$(FIRMWARE_NAME)'. Available firmwares are: $(FIRMWARES)."
	@exit 1
else ifneq (,$(INVALID_MOD_NAMES))
	@echo "Invalid mod(s) '$(INVALID_MOD_NAMES)'. Available mods are: $(MOD_LIST)."
	@exit 1
endif
	./scripts/create_firmware.sh $< $(BUILD_DIR) $@ $(OVERLAYS)

.PHONY: build
build: $(OUTPUT_FILE)

EXTRACT_DIR := tmp/extracted-$(FIRMWARE_VERSION)

.PHONY: extract
extract: firmware/$(FIRMWARE_FILE) tools
	./scripts/extract_squashfs.sh $< $(EXTRACT_DIR)

.PHONY: overlays
overlays:
	@echo $(OVERLAYS)

.PHONY: mods
mods:
	@echo "Available firmwares: $(FIRMWARES)"
	@echo "Available mods: $(MOD_LIST)"

# ================= Tools =================

.PHONY: tools
tools: tools/rk2918_tools tools/upfile tools/resource_tool

tools/%: FORCE
	make -C $@

# =============== Firmware ===============

.PHONY: firmware
firmware: firmware/$(FIRMWARE_FILE)

firmware/$(FIRMWARE_FILE): FORCE
	bash scripts/helpers/cache_file.sh "$@" \
		"https://public.resource.snapmaker.com/firmware/U1/$(FIRMWARE_FILE)" \
		"$(FIRMWARE_SHA256)"

# ================= Test =================

test: test-downloads test-build-guards firmware/$(FIRMWARE_FILE) tools
	make -C tools test FIRMWARE_FILE=$(CURDIR)/firmware/$(FIRMWARE_FILE)

.PHONY: test-downloads test-build-guards
test-downloads:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/cache_file_test.py

test-build-guards:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/build_guard_test.py

# ================= Helpers =================

.PHONY: changelog
changelog:
	@echo "## Changes since last release\n"
	@git log $$(git describe --tags --abbrev=0)..HEAD --pretty=format:"- %s (%h) by @%an"

.PHONY: FORCE
FORCE:
