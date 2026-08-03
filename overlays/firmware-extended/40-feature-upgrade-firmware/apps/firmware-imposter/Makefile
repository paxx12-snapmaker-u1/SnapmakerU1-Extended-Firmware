CROSS_COMPILE ?=
CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

TARGET = libfirmware-imposter.so

CFLAGS = -Wall -Wextra -O2 -fPIC
LDFLAGS = -shared -lpthread -ldl

SRCS = main.c
OBJS = $(SRCS:.c=.o)

PREFIX ?= /usr/local
LIBDIR ?= $(PREFIX)/lib

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
	install -d $(DESTDIR)$(LIBDIR)
	install -m 755 $(TARGET) $(DESTDIR)$(LIBDIR)/

.PHONY: all clean strip install
