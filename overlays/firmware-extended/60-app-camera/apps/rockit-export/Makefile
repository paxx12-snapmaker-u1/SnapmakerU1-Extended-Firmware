# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

CROSS_COMPILE ?=
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

TARGET = librockit-export.so

# v4l2-mpp checkout, for its socket and logging headers
V4L2_MPP_DIR ?=

CFLAGS = -Wall -Wextra -Wno-unused-function -O2 -fPIC
CFLAGS += -I$(V4L2_MPP_DIR)/common -I$(V4L2_MPP_DIR)/common/capture-common -I$(V4L2_MPP_DIR)/common/stream-common
LDFLAGS = -shared -lpthread -ldl

SRCS = main.c
OBJS = $(SRCS:.c=.o)

PREFIX ?= /usr/local
LIBDIR ?= $(PREFIX)/lib

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LDFLAGS)

%.o: %.c
	$(if $(V4L2_MPP_DIR),,$(error V4L2_MPP_DIR is not set))
	$(CC) $(CFLAGS) -c $< -o $@

strip: $(TARGET)
	$(STRIP) $(TARGET)

clean:
	rm -f $(OBJS) $(TARGET)

install: $(TARGET)
	install -d $(DESTDIR)$(LIBDIR)
	install -m 755 $(TARGET) $(DESTDIR)$(LIBDIR)/

.PHONY: all clean strip install
