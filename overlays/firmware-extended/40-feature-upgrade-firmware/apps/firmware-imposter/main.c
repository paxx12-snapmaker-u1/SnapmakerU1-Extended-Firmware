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

#define LOG_PREFIX "[firmware-imposter] "
#define LOG_DEBUG(fmt, ...) do { if (cfg_debug) fprintf(stderr, LOG_PREFIX "DEBUG[%d]: " fmt "\n", getpid(), ##__VA_ARGS__); } while(0)
#define LOG_INFO(fmt, ...) fprintf(stderr, LOG_PREFIX "INFO[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)

typedef void CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLINFO;

#define CURLOPT_URL 10002
#define CURLOPT_HTTPHEADER 10023
#define CURLINFO_EFFECTIVE_URL 0x100001
#define CURLE_COULDNT_CONNECT 7

struct curl_slist {
    char *data;
    struct curl_slist *next;
};

typedef CURLcode (*setopt_fn)(CURL *, CURLoption, ...);
typedef CURLcode (*perform_fn)(CURL *);
typedef CURLcode (*getinfo_fn)(CURL *, CURLINFO, ...);
typedef struct curl_slist *(*slist_append_fn)(struct curl_slist *, const char *);

static setopt_fn real_setopt;
static perform_fn real_perform;
static getinfo_fn real_getinfo;
static slist_append_fn real_slist_append;

static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static bool g_active = false;
static bool g_block = false;

static int cfg_debug = 0;
static char cfg_url_match[256] = "";
static char cfg_rewrite_url[2048] = "";
static struct curl_slist *g_strip_headers = NULL;

static void init_once(void)
{
    const char *env;

    real_setopt = (setopt_fn)dlsym(RTLD_NEXT, "curl_easy_setopt");
    real_perform = (perform_fn)dlsym(RTLD_NEXT, "curl_easy_perform");
    real_getinfo = (getinfo_fn)dlsym(RTLD_NEXT, "curl_easy_getinfo");
    real_slist_append = (slist_append_fn)dlsym(RTLD_NEXT, "curl_slist_append");

    char self_path[256];
    ssize_t len = readlink("/proc/self/exe", self_path, sizeof(self_path) - 1);
    if (len >= 0) {
        self_path[len] = '\0';
        const char *base = strrchr(self_path, '/');
        base = base ? base + 1 : self_path;
        if (strcmp(base, "unisrv") != 0) {
            LOG_DEBUG("Current process is not unisrv (%s), passthrough", base);
            return;
        }
    }

    env = getenv("FW_IMPOSTER_DEBUG");
    if (env && atoi(env))
        cfg_debug = 1;

    env = getenv("FW_UPDATE_URL");
    if (env && strlen(env) > 0) {
        strncpy(cfg_url_match, env, sizeof(cfg_url_match) - 1);
        cfg_url_match[sizeof(cfg_url_match) - 1] = '\0';
    }

    env = getenv("FW_UPDATE_BLOCK");
    if (env && atoi(env))
        g_block = true;

    env = getenv("FW_UPDATE_REWRITE_URL");
    if (env && strlen(env) > 0) {
        strncpy(cfg_rewrite_url, env, sizeof(cfg_rewrite_url) - 1);
        cfg_rewrite_url[sizeof(cfg_rewrite_url) - 1] = '\0';
        g_active = true;
    }

    if (g_block) {
        g_active = true;
        LOG_INFO("Active: blocking requests matching '%s'", cfg_url_match);
    } else if (g_active) {
        g_strip_headers = real_slist_append(NULL, "Authorization:");
        LOG_INFO("Active: rewriting URLs matching '%s' to '%s'", cfg_url_match, cfg_rewrite_url);
    } else {
        LOG_DEBUG("FW_UPDATE_REWRITE_URL not set, passthrough");
    }
}

CURLcode curl_easy_perform(CURL *handle)
{
    pthread_once(&g_once, init_once);

    if (!g_active || handle == NULL)
        return real_perform(handle);

    char *url = NULL;
    real_getinfo(handle, CURLINFO_EFFECTIVE_URL, &url);

    LOG_INFO("Performing request to %s", url ? url : "NULL");

    if (url && strstr(url, cfg_url_match) != NULL) {
        if (g_block) {
            LOG_INFO("Blocking request to %s", url);
            return CURLE_COULDNT_CONNECT;
        }
        LOG_INFO("Rewriting %s -> %s (Authorization stripped)", url, cfg_rewrite_url);
        real_setopt(handle, CURLOPT_URL, cfg_rewrite_url);
        real_setopt(handle, CURLOPT_HTTPHEADER, g_strip_headers);
    }

    return real_perform(handle);
}
