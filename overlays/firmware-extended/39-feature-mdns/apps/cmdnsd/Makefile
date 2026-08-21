CROSS_COMPILE ?=
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

TARGET = cmdnsd

CFLAGS = -Wall -O2

SRCS = cmdnsd.c
OBJS = $(SRCS:.c=.o)

PREFIX ?= /usr/local
SBINDIR ?= $(PREFIX)/sbin

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(OBJS) -o $@

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

strip: $(TARGET)
	$(STRIP) $(TARGET)

clean:
	rm -f $(OBJS) $(TARGET)

install: $(TARGET)
	install -d $(DESTDIR)$(SBINDIR)
	install -m 755 $(TARGET) $(DESTDIR)$(SBINDIR)/

.PHONY: all clean strip install
