// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12, @james-coder

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <openssl/md5.h>
#include <sys/stat.h>
#include "helpers.h"

typedef struct __attribute__((packed)) {
  uint32_t magic;
  uint16_t magic_ver;
  uint16_t checksum;
  char version[24];
  char build_date[14];
  uint16_t files;
  char reserved[16];
} UPFILE_HEADER;

typedef struct __attribute__((packed)) {
  uint16_t type;
  uint16_t checksum;
  uint64_t offset;
  uint32_t size;
  uint8_t md5[16];
} UPFILE_ENTRY;

#define UPFILE_MAGIC 0x4b4d4e53 // "SNMK"
#define UPFILE_FILE_COUNT 4

static const char *FILE_TYPE_STRINGS[UPFILE_FILE_COUNT] = {
  "SOC_FW",
  "MCU1_FW",
  "MCU2_FW",
  "MCU_DESC"
};

static const char *FILE_NAMES[UPFILE_FILE_COUNT] = {
  "update.img",
  "at32f403a.bin",
  "at32f415.bin",
  "MCU_DESC"
};

static int info(const char *infile, const char *outdir, void (*file_fnc)(const char *filename, void *data, size_t size)) {
  FILE *fp = fopen(infile, "rb");
  if (!fp) {
    perror("fopen");
    return -1;
  }

  if (outdir) {
    mkdir(outdir, 0755);
    int ret = chdir(outdir);
    if (ret != 0) {
      perror("chdir");
      goto error;
    }
  }

  UPFILE_HEADER header;
  if (!read_encoded_data(fp, &header, sizeof(UPFILE_HEADER))) {
    fprintf(stderr, "Failed to read header\n");
    goto error;
  }
  if (header.magic != UPFILE_MAGIC) {
    fprintf(stderr, "Invalid magic: 0x%08x\n", header.magic);
    goto error;
  }
  if (!validate_checksum(&header, sizeof(UPFILE_HEADER), &header.checksum)) {
    fprintf(stderr, "Header checksum validation failed\n");
    goto error;
  }

  printf("UPFILE Header:\n");
  printf("  Magic:\t0x%08x\n", header.magic);
  printf("  Magic Ver:\t0x%04x\n", be_to_host16(header.magic_ver));
  printf("  Version:\t%*s\n", sizeof(header.version), header.version);
  printf("  Build Date:\t%*s\n", sizeof(header.build_date), header.build_date);
  printf("  Checksum:\t0x%04x\n", be_to_host16(header.checksum));
  printf("  Files:\t%u\n", be_to_host16(header.files));

  if (file_fnc) {
    file_fnc("UPFILE_VERSION", header.version, trim_right(header.version, sizeof(header.version)));
    file_fnc("UPFILE_BUILD_DATE", header.build_date, trim_right(header.build_date, sizeof(header.build_date)));
  }

  uint16_t files = be_to_host16(header.files);

  if (files > UPFILE_FILE_COUNT) {
    fprintf(stderr, "Too many files found: %u\n", files);
    goto error;
  }

  for (uint16_t i = 0; i < files; i++) {
    UPFILE_ENTRY entry;
    if (fseek(fp, sizeof(UPFILE_HEADER) + i * sizeof(UPFILE_ENTRY), SEEK_SET) != 0) {
      perror("fseek");
      goto error;
    }
    if (!read_encoded_data(fp, &entry, sizeof(UPFILE_ENTRY))) {
      fprintf(stderr, "Failed to read file entry %u\n", i);
      goto error;
    }
    if (!validate_checksum(&entry, sizeof(UPFILE_ENTRY), &entry.checksum)) {
      fprintf(stderr, "File entry %u checksum validation failed\n", i);
      goto error;
    }
    printf("File Entry %u:\n", i);
    printf("  Type:\t\t%u\n", be_to_host16(entry.type));
    printf("  Offset:\t0x%016llx\n", (unsigned long long)be_to_host64(entry.offset));
    printf("  Size:\t\t%u\n", be_to_host32(entry.size));
    printf("  Checksum:\t0x%04x\n", be_to_host16(entry.checksum));
    printf("  MD5:\t\t");
    for (size_t j = 0; j < sizeof(entry.md5); j++) {
      printf("%02x", (uint8_t)entry.md5[j]);
    }
    printf("\n");

    if (fseek(fp, be_to_host64(entry.offset), SEEK_SET) != 0) {
      perror("fseek");
      goto error;
    }

    uint8_t *data = malloc(be_to_host32(entry.size));
    if (!data) {
      perror("malloc");
      goto error;
    }

    if (fread(data, 1, be_to_host32(entry.size), fp) != be_to_host32(entry.size)) {
      fprintf(stderr, "Failed to read data for file entry %u\n", i);
      free(data);
      goto error;
    }

    if (!validate_md5(data, be_to_host32(entry.size), entry.md5)) {
      fprintf(stderr, "File entry %u MD5 validation failed\n", i);
      free(data);
      goto error;
    }

    if (file_fnc) {
      file_fnc(FILE_NAMES[i], data, be_to_host32(entry.size));
    }
    free(data);
  }

  fclose(fp);
  return 0;

error:
  fclose(fp);
  return -1;
}

static void unpack_file(const char *filename, void *data, size_t size) {
  FILE *outfp = fopen(filename, "wb");
  if (!outfp) {
    perror("fopen");
    return;
  }
  if (fwrite(data, 1, size, outfp) != size) {
    fprintf(stderr, "Failed to write to %s\n", filename);
  } else {
    printf("Extracted %s (%zu bytes)\n", filename, size);
  }
  fclose(outfp);
}

static int unpack(const char *infile, const char *outdir) {
  return info(infile, outdir, unpack_file);
}

static int pack_file(FILE *outfp, int index, int type, const char *filename, uint64_t *data_offset) {
  printf("Packing file %s as type %d\n", filename, type);
  // Read input file
  FILE *infp = fopen(filename, "rb");
  if (!infp) {
    perror("fopen");
    return -1;
  }
  fseek(infp, 0, SEEK_END);
  size_t file_size = ftell(infp);
  fseek(infp, 0, SEEK_SET);
  uint8_t *file_data = malloc(file_size);
  if (!file_data) {
    perror("malloc");
    fclose(infp);
    return -1;
  }
  if (fread(file_data, 1, file_size, infp) != file_size) {
    fprintf(stderr, "Failed to read %s\n", filename);
    free(file_data);
    fclose(infp);
    return -1;
  }
  fclose(infp);

  // Create file entry
  UPFILE_ENTRY entry = {0};
  entry.type = host_to_be16(type);
  entry.offset = host_to_be64(*data_offset);
  entry.size = host_to_be32(file_size);
  calculate_md5(file_data, file_size, entry.md5);
  entry.checksum = host_to_be16(calculate_checksum(&entry, sizeof(UPFILE_ENTRY)));

  // Write file entry
  if (fseek(outfp, sizeof(UPFILE_HEADER) + index * sizeof(UPFILE_ENTRY), SEEK_SET) != 0) {
    perror("fseek");
    goto error_data;
  }
  if (!write_encoded_data(outfp, &entry, sizeof(UPFILE_ENTRY))) {
    fprintf(stderr, "Failed to write file entry for %s\n", filename);
    goto error_data;
  }

  // Write file data
  if (fseek(outfp, *data_offset, SEEK_SET) != 0) {
    perror("fseek");
    goto error_data;
  }
  if (fwrite(file_data, 1, file_size, outfp) != file_size) {
    fprintf(stderr, "Failed to write data for %s\n", filename);
    goto error_data;
  }

  *data_offset += file_size;
  free(file_data);
  return 0;

error_data:
  free(file_data);
  return -1;
}

static int read_string_from_file(const char *filename, char *buffer, size_t buffer_size) {
  FILE *fp = fopen(filename, "r");
  if (!fp) {
    return -1;
  }
  memset(buffer, 0, buffer_size);
  fread(buffer, 1, buffer_size, fp);
  fclose(fp);
  trim_right(buffer, buffer_size);
  return 0;
}

static int pack(const char *outfile, const char *indir) {
  FILE *outfp = fopen(outfile, "wb");
  if (!outfp) {
    perror("fopen");
    return -1;
  }

  if (indir) {
    int ret = chdir(indir);
    if (ret != 0) {
      perror("chdir");
      goto error;
    }
  }

  // Create upfile header
  UPFILE_HEADER header = {0};
  header.magic = UPFILE_MAGIC;
  header.magic_ver = host_to_be16(1);
  header.files = host_to_be16(UPFILE_FILE_COUNT);
  if (read_string_from_file("UPFILE_VERSION", header.version, sizeof(header.version)) != 0) {
    fprintf(stderr, "Error: UPFILE_VERSION not found\n");
    goto error;
  }
  if (read_string_from_file("UPFILE_BUILD_DATE", header.build_date, sizeof(header.build_date)) != 0) {
    fprintf(stderr, "Error: UPFILE_BUILD_DATE not found\n");
    goto error;
  }

  header.checksum = host_to_be16(calculate_checksum(&header, sizeof(UPFILE_HEADER)));

  // Write upfile header
  if (!write_encoded_data(outfp, &header, sizeof(UPFILE_HEADER))) {
    fprintf(stderr, "Failed to write header\n");
    goto error;
  }

  printf("Packing UPFILE with %zu files\n", UPFILE_FILE_COUNT);

  uint64_t data_offset = sizeof(UPFILE_HEADER) + UPFILE_FILE_COUNT * sizeof(UPFILE_ENTRY);

  for (int i = 0; i < UPFILE_FILE_COUNT; i++) {
    int ret = pack_file(outfp, i, i, FILE_NAMES[i], &data_offset);
    if (ret != 0) {
      goto error;
    }
  }

  printf("Packed UPFILE to %s\n", outfile);

  fclose(outfp);
  return 0;

error:
  fclose(outfp);
  return -1;
}

int main(int argc, char *argv[]) {
  if (argc == 3 && strcmp(argv[1], "info") == 0) {
    return info(argv[2], NULL, NULL);
  } else if ((argc == 3 || argc == 4) && strcmp(argv[1], "unpack") == 0) {
    return unpack(argv[2], argc == 4 ? argv[3] : NULL);
  } else if ((argc == 3 || argc == 4) && strcmp(argv[1], "pack") == 0) {
    return pack(argv[2], argc == 4 ? argv[3] : NULL);
  }

  fprintf(stderr, "Usage: %s info <upfile>\n", argv[0]);
  fprintf(stderr, "       %s unpack <upfile> [outdir]\n", argv[0]);
  fprintf(stderr, "       %s pack <outfile> [indir]\n", argv[0]);
  return -1;
}
