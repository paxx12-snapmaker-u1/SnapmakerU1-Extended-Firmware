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

EXTRACT_DIR := tmp/extracted

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

firmware/$(FIRMWARE_FILE):
	@mkdir -p firmware
	wget -O $@.tmp "https://public.resource.snapmaker.com/firmware/U1/$(FIRMWARE_FILE)"
	echo "$(FIRMWARE_SHA256)  $@.tmp" | sha256sum -c --quiet
	mv $@.tmp $@

# ================= Test =================

test: firmware/$(FIRMWARE_FILE)
	make -C tools test FIRMWARE_FILE=$(CURDIR)/firmware/$(FIRMWARE_FILE)

# ================= Helpers =================

.PHONY: changelog
changelog:
	@echo "## Changes since last release\n"
	@git log $$(git describe --tags --abbrev=0)..HEAD --pretty=format:"- %s (%h) by @%an"

.PHONY: FORCE
FORCE:
