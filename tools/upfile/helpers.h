// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12, @james-coder

#pragma once

#ifdef __BIG_ENDIAN__
static uint64_t swap_be64(uint64_t val) {
  return val;
}
static uint32_t swap_be32(uint32_t val) {
  return val;
}
static uint16_t swap_be16(uint16_t val) {
  return val;
}
#else // Little-endian
static uint64_t swap_be64(uint64_t val) {
  return ((val & 0xFF00000000000000) >> 56) |
         ((val & 0x00FF000000000000) >> 40) |
         ((val & 0x0000FF0000000000) >> 24) |
         ((val & 0x000000FF00000000) >> 8)  |
         ((val & 0x00000000FF000000) << 8)  |
         ((val & 0x0000000000FF0000) << 24) |
         ((val & 0x000000000000FF00) << 40) |
         ((val & 0x00000000000000FF) << 56);
}
static uint32_t swap_be32(uint32_t val) {
  return ((val & 0xFF000000) >> 24) |
         ((val & 0x00FF0000) >> 8)  |
         ((val & 0x0000FF00) << 8)  |
         ((val & 0x000000FF) << 24);
}
static uint16_t swap_be16(uint16_t val) {
  return ((val & 0xFF00) >> 8) |
         ((val & 0x00FF) << 8);
}
#endif

static uint64_t be_to_host64(uint64_t val) {
  return swap_be64(val);
}
static uint32_t be_to_host32(uint32_t val) {
  return swap_be32(val);
}
static uint16_t be_to_host16(uint16_t val) {
  return swap_be16(val);
}
static uint64_t host_to_be64(uint64_t val) {
  return swap_be64(val);
}
static uint32_t host_to_be32(uint32_t val) {
  return swap_be32(val);
}
static uint16_t host_to_be16(uint16_t val) {
  return swap_be16(val);
}

static uint8_t data_map[256] = {
  0x00, 0xa2, 0xe9, 0x0e, 0xde, 0x64, 0xee, 0x12,
  0x46, 0x3b, 0xe7, 0x79, 0xa5, 0x80, 0x55, 0x33,
  0x32, 0x3c, 0x0b, 0x7e, 0xce, 0xcc, 0x59, 0x37,
  0x01, 0x9b, 0x4e, 0xc1, 0xab, 0x18, 0x72, 0x11,
  0x9c, 0x5b, 0xe5, 0x8d, 0xe8, 0x0d, 0x69, 0x97,
  0x29, 0xd9, 0xc5, 0xaf, 0x4a, 0x61, 0x7a, 0x63,
  0x39, 0xc6, 0xbc, 0xd0, 0x92, 0xae, 0x7f, 0xdd,
  0x6c, 0x07, 0x3a, 0xb9, 0x91, 0xf7, 0xc7, 0x45,
  0x34, 0x40, 0xb5, 0x28, 0x44, 0xe6, 0xcb, 0x4d,
  0x3d, 0x9e, 0x94, 0x2b, 0x60, 0xd2, 0x2f, 0x3e,
  0x67, 0xd6, 0x52, 0xd3, 0xa8, 0xd4, 0xf8, 0x1a,
  0xda, 0xec, 0x70, 0x7c, 0xa0, 0x1d, 0x06, 0x4f,
  0x6e, 0x2d, 0xb8, 0x36, 0xf0, 0x43, 0x22, 0xbd,
  0xb4, 0x82, 0x9d, 0xeb, 0xa4, 0x56, 0x76, 0xe4,
  0x25, 0x15, 0x98, 0x71, 0xc3, 0x50, 0x49, 0x77,
  0x08, 0xfb, 0x47, 0x23, 0xaa, 0x8a, 0x20, 0xfc,
  0xf6, 0x8c, 0x85, 0x30, 0x1e, 0xc9, 0x62, 0xba,
  0x53, 0x0f, 0xf2, 0x57, 0x51, 0x7b, 0x13, 0x1f,
  0x96, 0xc0, 0x35, 0x73, 0x87, 0xdb, 0xb7, 0xa3,
  0xed, 0x90, 0x5f, 0x9a, 0xdc, 0xe3, 0xe1, 0x0a,
  0x1b, 0x17, 0xea, 0x41, 0xfe, 0x58, 0x26, 0xcd,
  0x05, 0xc2, 0x02, 0x88, 0x6a, 0x9f, 0x93, 0x74,
  0xe2, 0x2a, 0x09, 0xac, 0x81, 0x5c, 0x1c, 0xf3,
  0x8b, 0xfa, 0x84, 0xa1, 0xd7, 0x5d, 0xbe, 0x75,
  0x38, 0xe0, 0xa6, 0x2c, 0x2e, 0x66, 0x86, 0xef,
  0x54, 0x68, 0xb6, 0xa9, 0xf5, 0x0c, 0xf9, 0xb3,
  0xbf, 0x14, 0xb2, 0xfd, 0xbb, 0xd5, 0x04, 0xf4,
  0xa7, 0x48, 0xf1, 0x6d, 0x6b, 0x99, 0x7d, 0x4b,
  0xb0, 0xad, 0x21, 0x95, 0xd1, 0xcf, 0xc4, 0x24,
  0xca, 0x8e, 0xb1, 0x83, 0x16, 0xdf, 0x19, 0x10,
  0x27, 0x4c, 0x6f, 0x31, 0x3f, 0x5a, 0x65, 0x78,
  0xc8, 0xd8, 0x03, 0x89, 0x5e, 0x8f, 0x42, 0xff
};

#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]))

static void decode_data(void *datap, size_t size) {
  uint8_t *data = (uint8_t *)datap;
  for (size_t i = 0; i < size; i++) {
    data[i] = data_map[data[i]];
  }
}

static void encode_data(void *datap, size_t size) {
  uint8_t *data = (uint8_t *)datap;
  for (size_t i = 0; i < size; i++) {
    // Reverse lookup in data_map
    for (uint16_t j = 0; j < 256; j++) {
      if (data_map[j] == data[i]) {
        data[i] = (uint8_t)j;
        break;
      }
    }
  }
}

static bool read_encoded_data(FILE *fp, void *datap, size_t size) {
  if (fread(datap, 1, size, fp) != size) {
    return false;
  }
  decode_data(datap, size);
  return true;
}

static bool write_encoded_data(FILE *fp, const void *datap, size_t size) {
  void *buffer = malloc(size);
  if (!buffer) {
    return false;
  }
  memcpy(buffer, datap, size);
  encode_data(buffer, size);
  bool result = fwrite(buffer, 1, size, fp) == size;
  free(buffer);
  return result;
}

static uint16_t calculate_checksum(const void *datap, size_t len) {
  uint16_t sum = 0;
  const uint8_t *data = (const uint8_t *)datap;
  for (size_t i = 0; i < len; i++) {
    sum += (uint16_t)data[i];
  }
  return sum;
}

static bool validate_checksum(const void *datap, size_t len, uint16_t *checksum) {
  // persist checksum
  uint16_t saved = *checksum;
  *checksum = 0;
  uint16_t calculated = calculate_checksum(datap, len);
  *checksum = saved;

  if (calculated != be_to_host16(saved)) {
    fprintf(stderr, "Checksum mismatch: calculated 0x%04x, expected 0x%04x\n", calculated, be_to_host16(saved));
    return false;
  }
  return true;
}

static void calculate_md5(const void *datap, size_t len, uint8_t md5[16]) {
  MD5_CTX ctx;
  MD5_Init(&ctx);
  MD5_Update(&ctx, datap, len);
  MD5_Final(md5, &ctx);
}

static bool validate_md5(const void *datap, size_t len, const uint8_t md5[16]) {
  uint8_t digest[16];
  calculate_md5(datap, len, digest);
  return memcmp(digest, md5, 16) == 0;
}

static void print_hex(const uint8_t *data, size_t size) {
  for (size_t i = 0; i < size; i++) {
    printf("%02x ", data[i]);
    if ((i + 1) % 16 == 0) {
      // print printable characters
      printf("| ");
      for (size_t j = i - 15; j <= i; j++) {
        if (data[j] >= 32 && data[j] <= 126) {
          printf("%c", data[j]);
        } else {
          printf(".");
        }
      }
      printf("\n");
    }
  }
  if (size % 16 != 0) {
    printf("| ");
    for (size_t j = size - (size % 16); j < size; j++) {
      if (data[j] >= 32 && data[j] <= 126) {
        printf("%c", data[j]);
      } else {
        printf(".");
      }
    }
    printf("\n");
  }
}

static size_t trim_right(char *str, size_t max_len) {
  size_t len = strnlen(str, max_len);
  while (len > 0 && (str[len - 1] == '\n' || str[len - 1] == '\r' || str[len - 1] == ' ' || str[len - 1] == '\t')) {
    str[len - 1] = '\0';
    len--;
  }
  return len;
}
