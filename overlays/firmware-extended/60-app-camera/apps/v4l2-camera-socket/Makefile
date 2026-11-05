# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

CROSS_COMPILE ?=
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

TARGET = libv4l-camera-socket.so

CFLAGS = -Wall -Wextra -O2 -fPIC
LDFLAGS = -shared -lpthread

SRCS = main.c
OBJS = $(SRCS:.c=.o)

# libv4l2 loads its plugins from here, in name order: this one must sort
# before Rockchip's libv4l-mplane.so.
PLUGINDIR ?= /usr/lib/libv4l/plugins

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

strip: $(TARGET)
	$(STRIP) $(TARGET)

clean:
	rm -f $(OBJS) $(TARGET)

install: $(TARGET)
	install -d $(DESTDIR)$(PLUGINDIR)
	install -m 755 $(TARGET) $(DESTDIR)$(PLUGINDIR)/

.PHONY: all clean strip install
