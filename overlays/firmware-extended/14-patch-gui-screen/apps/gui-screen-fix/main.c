// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdbool.h>
#include <pthread.h>
#include <dlfcn.h>

#define LOG_PREFIX "[gui-screen-fix] "
#define LOG_DEBUG(fmt, ...) do { if (cfg_debug) fprintf(stderr, LOG_PREFIX "DEBUG[%d]: " fmt "\n", getpid(), ##__VA_ARGS__); } while (0)
#define LOG_INFO(fmt, ...) fprintf(stderr, LOG_PREFIX "INFO[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)

#define DEFAULT_DRM_STATUS_PATH "/sys/class/drm/card0-DPI-1/status"
#define DEFAULT_BACKLIGHT_PATH  "/sys/class/backlight/backlight/bl_power"

typedef FILE *(*fopen_fn)(const char *, const char *);
typedef size_t (*fwrite_fn)(const void *, size_t, size_t, FILE *);
typedef int (*fclose_fn)(FILE *);

static fopen_fn real_fopen = NULL;
static fopen_fn real_fopen64 = NULL;
static fwrite_fn real_fwrite = NULL;
static fclose_fn real_fclose = NULL;

static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;
static bool g_active = false;
static int cfg_debug = 0;

static const char *cfg_drm_status_path = DEFAULT_DRM_STATUS_PATH;
static const char *cfg_backlight_path = DEFAULT_BACKLIGHT_PATH;
static FILE *g_intercepted_fp = NULL;

static void init_once(void)
{
    const char *env;

    real_fopen = (fopen_fn)dlsym(RTLD_NEXT, "fopen");
    real_fopen64 = (fopen_fn)dlsym(RTLD_NEXT, "fopen64");
    if (!real_fopen64)
        real_fopen64 = real_fopen;
    real_fwrite = (fwrite_fn)dlsym(RTLD_NEXT, "fwrite");
    real_fclose = (fclose_fn)dlsym(RTLD_NEXT, "fclose");

    env = getenv("GUI_SCREEN_FIX_DEBUG");
    if (env && atoi(env))
        cfg_debug = 1;

    /* Do not activate when HelixScreen is bind-mounted over /usr/bin/gui */
    if (getenv("HELIX_SUPERVISED") != NULL || getenv("HELIX_DATA_DIR") != NULL) {
        LOG_DEBUG("HelixScreen detected, passthrough");
        return;
    }

    char self_path[256];
    ssize_t len = readlink("/proc/self/exe", self_path, sizeof(self_path) - 1);
    if (len >= 0) {
        self_path[len] = '\0';
        const char *base = strrchr(self_path, '/');
        base = base ? base + 1 : self_path;
        if (strcmp(base, "gui") != 0) {
            LOG_DEBUG("Current process is not gui (%s), passthrough", base);
            return;
        }
    } else {
        return;
    }

    env = getenv("GUI_SCREEN_FIX_DRM_PATH");
    if (env && env[0] != '\0')
        cfg_drm_status_path = env;

    env = getenv("GUI_SCREEN_FIX_BACKLIGHT_PATH");
    if (env && env[0] != '\0')
        cfg_backlight_path = env;

    g_active = true;
    LOG_INFO("Active: redirecting '%s' to '%s'", cfg_drm_status_path, cfg_backlight_path);
}

static FILE *handle_fopen(const char *pathname, const char *mode, fopen_fn fn)
{
    pthread_once(&g_once, init_once);

    if (!g_active || !pathname || !mode || strcmp(pathname, cfg_drm_status_path) != 0)
        return fn(pathname, mode);

    pthread_mutex_lock(&g_mutex);
    FILE *fp = fn(cfg_backlight_path, mode);
    if (fp) {
        g_intercepted_fp = fp;
        LOG_DEBUG("Redirected open('%s', '%s') -> '%s' (fp=%p)",
                  pathname, mode, cfg_backlight_path, (void *)fp);
    }
    pthread_mutex_unlock(&g_mutex);
    return fp;
}

FILE *fopen(const char *pathname, const char *mode)
{
    pthread_once(&g_once, init_once);
    return handle_fopen(pathname, mode, real_fopen);
}

FILE *fopen64(const char *pathname, const char *mode)
{
    pthread_once(&g_once, init_once);
    return handle_fopen(pathname, mode, real_fopen64);
}

size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream)
{
    pthread_once(&g_once, init_once);

    if (!g_active || stream != g_intercepted_fp)
        return real_fwrite(ptr, size, nmemb, stream);

    pthread_mutex_lock(&g_mutex);
    if (stream != g_intercepted_fp) {
        pthread_mutex_unlock(&g_mutex);
        return real_fwrite(ptr, size, nmemb, stream);
    }

    size_t total = size * nmemb;
    if (total >= 3 && memcmp(ptr, "off", 3) == 0) {
        /* Stock gui system_module_force_screen_on_off(0) writes "off" to
         * /sys/class/drm/card0-DPI-1/status, which triggers vop2_crtc_atomic_disable
         * and wedges rk_iommu ff407e00.iommu (-EBUSY). Translate to "1" on
         * /sys/class/backlight/backlight/bl_power (matching stock
         * system_module_set_screen_backlight_on_off(0)) so only the physical
         * backlight turns off while VOP2 CRTC vp0 and /dev/fb0 stay alive.
         */
        size_t w = real_fwrite("1", 1, 1, stream);
        LOG_DEBUG("Translated DRM 'off' -> backlight bl_power '1' (w=%zu)", w);
        pthread_mutex_unlock(&g_mutex);
        return (w == 1) ? nmemb : 0;
    } else if (total >= 2 && memcmp(ptr, "on", 2) == 0) {
        size_t w = real_fwrite("0", 1, 1, stream);
        LOG_DEBUG("Translated DRM 'on' -> backlight bl_power '0' (w=%zu)", w);
        pthread_mutex_unlock(&g_mutex);
        return (w == 1) ? nmemb : 0;
    }

    pthread_mutex_unlock(&g_mutex);
    return real_fwrite(ptr, size, nmemb, stream);
}

int fclose(FILE *stream)
{
    pthread_once(&g_once, init_once);

    if (g_active && stream == g_intercepted_fp) {
        pthread_mutex_lock(&g_mutex);
        if (stream == g_intercepted_fp)
            g_intercepted_fp = NULL;
        pthread_mutex_unlock(&g_mutex);
    }

    return real_fclose(stream);
}
