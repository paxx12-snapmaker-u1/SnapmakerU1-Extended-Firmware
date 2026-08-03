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
typedef int CURLINFO;

#define CURLINFO_EFFECTIVE_URL 0x100001
#define CURLE_COULDNT_CONNECT 7

typedef CURLcode (*perform_fn)(CURL *);
typedef CURLcode (*getinfo_fn)(CURL *, CURLINFO, ...);

static perform_fn real_perform;
static getinfo_fn real_getinfo;

static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static bool g_active = false;

static int cfg_debug = 0;
static char cfg_url_match[256] = "";

static void init_once(void)
{
    const char *env;

    real_perform = (perform_fn)dlsym(RTLD_NEXT, "curl_easy_perform");
    real_getinfo = (getinfo_fn)dlsym(RTLD_NEXT, "curl_easy_getinfo");

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
    if (env && atoi(env)) {
        g_active = true;
        LOG_INFO("Active: blocking requests matching '%s'", cfg_url_match);
    } else {
        LOG_DEBUG("FW_UPDATE_BLOCK not set, passthrough");
    }
}

CURLcode curl_easy_perform(CURL *handle)
{
    pthread_once(&g_once, init_once);

    if (!g_active || handle == NULL)
        return real_perform(handle);

    char *url = NULL;
    real_getinfo(handle, CURLINFO_EFFECTIVE_URL, &url);

    if (url && strstr(url, cfg_url_match) != NULL) {
        LOG_INFO("Blocking request to %s", url);
        return CURLE_COULDNT_CONNECT;
    }

    return real_perform(handle);
}
