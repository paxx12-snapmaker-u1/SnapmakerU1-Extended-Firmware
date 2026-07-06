include vars.mk

all: tools

# ================= Build Tools =================

OUTPUT_FILE := firmware/firmware.bin
BUILD_DIR ?= tmp/firmware

ifneq (,$(PROFILE))
PROFILE_PARTS := $(subst -, ,$(PROFILE))
FIRMWARE_NAME := $(firstword $(PROFILE_PARTS))
PROFILE_NAMES := $(wordlist 2,$(words $(PROFILE_PARTS)),$(PROFILE_PARTS))
OVERLAYS += $(wildcard overlays/common/*/)
OVERLAYS += $(wildcard overlays/firmware-$(FIRMWARE_NAME)/*/)
OVERLAYS += $(foreach p,$(PROFILE_NAMES),$(wildcard overlays/profile-$(p)/*/))
endif

FIRMWARES := $(patsubst overlays/firmware-%,%,$(wildcard overlays/firmware-*))
PROFILE_LIST := $(patsubst overlays/profile-%,%,$(wildcard overlays/profile-*))
INVALID_PROFILE_NAMES := $(filter-out $(PROFILE_LIST),$(PROFILE_NAMES))

$(OUTPUT_FILE): firmware/$(FIRMWARE_FILE) tools
ifeq (,$(PROFILE))
	@echo "Please specify a profile using 'make PROFILE=<firmware>[-<profile>]*'. Available firmwares are: $(FIRMWARES). Available profiles are: $(PROFILE_LIST)."
	@exit 1
else ifeq (,$(filter $(FIRMWARE_NAME),$(FIRMWARES)))
	@echo "Invalid firmware '$(FIRMWARE_NAME)'. Available firmwares are: $(FIRMWARES)."
	@exit 1
else ifneq (,$(INVALID_PROFILE_NAMES))
	@echo "Invalid profile(s) '$(INVALID_PROFILE_NAMES)'. Available profiles are: $(PROFILE_LIST)."
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

.PHONY: profiles
profiles:
	@echo "Available firmwares: $(FIRMWARES)"
	@echo "Available profiles: $(PROFILE_LIST)"

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
