// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12 @RrrrobertB1992

// LD_PRELOAD shim for stock 2.1.0 unisrv, which captures through librockit
// (RK_MPI_VI_* / RK_MPI_VENC_*) instead of libv4l2. It replaces the Rockit
// video input, encoder and bind calls unisrv uses and serves them from the
// capture-v4l2-raw-mpp sockets, so unisrv never opens the ISP itself:
//
//   VI_GetChnFrame          <- raw NV12 frame socket (scaled/converted on demand)
//   VENC_GetStream (JPEG)   <- one-shot JPEG snapshot socket
//   VENC_GetStream (H.264)  <- H.264 stream socket, one access unit per call
//
// Only active inside unisrv; every other process gets the real librockit.

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

// ---------------------------------------------------------------------------
// Minimal Rockit MPI ABI (aarch64). Only the fields this shim reads or writes
// are named. Offsets match the Rockit SDK headers and were checked against how
// stock 2.1.0 unisrv accesses them.
// ---------------------------------------------------------------------------

typedef int32_t RK_S32;
typedef uint32_t RK_U32;
typedef uint64_t RK_U64;
typedef void *MB_BLK;

#define RK_SUCCESS 0
#define RK_FAILURE (-1)
#define RK_ERR_VI_NOT_CONFIG ((RK_S32)0xA0088007) // unisrv compares GetDevAttr against this

#define RK_ID_VI 8
#define RK_ID_VENC 4

#define RK_VIDEO_ID_AVC 8
#define RK_VIDEO_ID_MJPEG 9
#define RK_VIDEO_ID_JPEG 15

#define RK_FMT_YUV420SP 0 // NV12
#define RK_FMT_YUV420P 4  // I420

#define H264E_NALU_PSLICE 1
#define H264E_NALU_IDRSLICE 5
#define JPEGE_PACK_PIC 8

typedef struct {
    RK_S32 enModId;
    RK_S32 s32DevId;
    RK_S32 s32ChnId;
} MPP_CHN_S;

typedef struct {
    MB_BLK pMbBlk;
    RK_U32 u32Width;
    RK_U32 u32Height;
    RK_U32 u32VirWidth;
    RK_U32 u32VirHeight;
    RK_S32 enField;
    RK_S32 enPixelFormat;
    RK_S32 enVideoFormat;
    RK_S32 enCompressMode;
    RK_S32 enDynamicRange;
    RK_S32 enColorGamut;
    void *pVirAddr[2];
    RK_U32 u32TimeRef;
    RK_U64 u64PTS;
    RK_U64 u64PrivateData;
    RK_U32 u32FrameFlag;
} VIDEO_FRAME_INFO_S;

typedef struct {
    RK_S32 u32PackType;
    RK_U32 u32PackOffset;
    RK_U32 u32PackLength;
} VENC_PACK_INFO_S;

typedef struct {
    MB_BLK pMbBlk;
    RK_U32 u32Len;
    RK_U64 u64PTS;
    RK_S32 bFrameEnd;
    RK_S32 bStreamEnd;
    RK_S32 DataType;
    RK_U32 u32Offset;
    RK_U32 u32DataNum;
    VENC_PACK_INFO_S stPackInfo[8];
} VENC_PACK_S;

// Only the leading fields are touched; the rest (stream info unions) is left
// as the caller initialised it.
typedef struct {
    VENC_PACK_S *pstPack;
    uint8_t reserved0[8];
    RK_U32 u32PackCount;
    RK_U32 u32Seq;
} VENC_STREAM_HEAD_S;

#define VI_DEV_ATTR_SIZE 64
#define VI_CHN_ATTR_PIXFMT_OFFSET 8
#define VI_CHN_ATTR_ENTITY_OFFSET 64
#define VI_CHN_ATTR_ENTITY_SIZE 32
#define VENC_CHN_ATTR_WIDTH_OFFSET 32
#define VENC_CHN_ATTR_HEIGHT_OFFSET 36

_Static_assert(sizeof(MPP_CHN_S) == 12, "MPP_CHN_S");
_Static_assert(sizeof(VIDEO_FRAME_INFO_S) == 96, "VIDEO_FRAME_INFO_S");
_Static_assert(offsetof(VIDEO_FRAME_INFO_S, enPixelFormat) == 28, "enPixelFormat");
_Static_assert(offsetof(VIDEO_FRAME_INFO_S, pVirAddr) == 48, "pVirAddr");
_Static_assert(offsetof(VIDEO_FRAME_INFO_S, u64PTS) == 72, "u64PTS");
_Static_assert(sizeof(VENC_PACK_S) == 144, "VENC_PACK_S");
_Static_assert(offsetof(VENC_PACK_S, u64PTS) == 16, "pack u64PTS");
_Static_assert(offsetof(VENC_PACK_S, DataType) == 32, "pack DataType");
_Static_assert(offsetof(VENC_STREAM_HEAD_S, u32PackCount) == 16, "u32PackCount");

// ---------------------------------------------------------------------------
// Config and logging
// ---------------------------------------------------------------------------

static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static bool g_active;
static bool g_debug;
static char g_log_file[256];
static char g_jpeg_sock[108] = "/tmp/capture-mipi-jpeg.sock";
static char g_h264_sock[108] = "/tmp/capture-mipi-h264.sock";
static char g_raw_sock[108] = "/tmp/capture-mipi-raw.sock";
static RK_U32 g_raw_width = 1920;
static RK_U32 g_raw_height = 1080;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;

static void logmsg(int prio, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    syslog(prio, "%s", buf);
    fprintf(stderr, "[rockit-imposter] %s\n", buf);
    if (g_log_file[0]) {
        static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
        pthread_mutex_lock(&log_lock);
        FILE *f = fopen(g_log_file, "a");
        if (f) {
            if (ftell(f) > 1024 * 1024) { // keep the log small: start over past 1 MB
                fclose(f);
                f = fopen(g_log_file, "w");
            }
        }
        if (f) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            struct tm tm;
            localtime_r(&ts.tv_sec, &tm);
            fprintf(f, "%02d:%02d:%02d.%03ld [%d] %s\n", tm.tm_hour, tm.tm_min, tm.tm_sec,
                    ts.tv_nsec / 1000000, (int)getpid(), buf);
            fclose(f);
        }
        pthread_mutex_unlock(&log_lock);
    }
}

#define IMP_DBG(...) do { if (g_debug) logmsg(LOG_DEBUG, __VA_ARGS__); } while (0)
#define IMP_INF(...) logmsg(LOG_INFO, __VA_ARGS__)
#define IMP_ERR(...) logmsg(LOG_ERR, __VA_ARGS__)

static void env_str(const char *name, char *dst, size_t size)
{
    const char *v = getenv(name);
    if (v && *v) {
        strncpy(dst, v, size - 1);
        dst[size - 1] = '\0';
    }
}

static void init_once(void)
{
    char exe[256];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    const char *base = "";
    if (n > 0) {
        exe[n] = '\0';
        base = strrchr(exe, '/');
        base = base ? base + 1 : exe;
    }

    const char *disable = getenv("ROCKIT_IMPOSTER_DISABLE");
    g_active = strcmp(base, "unisrv") == 0 && !(disable && *disable && *disable != '0');
    if (!g_active) {
        return;
    }

    openlog("rockit-imposter", LOG_PID, LOG_DAEMON);
    const char *dbg = getenv("ROCKIT_IMPOSTER_DEBUG");
    g_debug = dbg && *dbg && *dbg != '0';
    env_str("ROCKIT_IMPOSTER_JPEG_SOCKET", g_jpeg_sock, sizeof(g_jpeg_sock));
    env_str("ROCKIT_IMPOSTER_H264_SOCKET", g_h264_sock, sizeof(g_h264_sock));
    env_str("ROCKIT_IMPOSTER_RAW_SOCKET", g_raw_sock, sizeof(g_raw_sock));
    env_str("ROCKIT_IMPOSTER_LOG_FILE", g_log_file, sizeof(g_log_file));
    const char *w = getenv("ROCKIT_IMPOSTER_WIDTH");
    const char *h = getenv("ROCKIT_IMPOSTER_HEIGHT");
    if (w && atoi(w) > 0) g_raw_width = (RK_U32)atoi(w);
    if (h && atoi(h) > 0) g_raw_height = (RK_U32)atoi(h);

    IMP_INF("active in %s: jpeg=%s h264=%s raw=%s (%ux%u nv12) debug=%d",
            base, g_jpeg_sock, g_h264_sock, g_raw_sock, g_raw_width, g_raw_height, g_debug);
}

static bool active(void)
{
    pthread_once(&g_once, init_once);
    return g_active;
}

// Forward a call to the real librockit when not inside unisrv.
#define REAL(ret, name, ...)                                                   \
    static ret (*real_fn)(__VA_ARGS__);                                        \
    if (!real_fn) real_fn = (ret (*)(__VA_ARGS__))dlsym(RTLD_NEXT, #name);

static uint64_t now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000ull + (uint64_t)ts.tv_nsec / 1000;
}

// ---------------------------------------------------------------------------
// Memory blocks handed to unisrv (resolved by RK_MPI_MB_Handle2VirAddr)
// ---------------------------------------------------------------------------

#define FAKE_MB_MAGIC 0x524b4d42u // "RKMB"

typedef struct {
    uint32_t magic;
    size_t size;
    uint8_t data[];
} fake_mb_t;

static fake_mb_t *mb_alloc(size_t size)
{
    fake_mb_t *mb = malloc(sizeof(*mb) + size);
    if (mb) {
        mb->magic = FAKE_MB_MAGIC;
        mb->size = size;
    }
    return mb;
}

static fake_mb_t *mb_get(MB_BLK blk)
{
    fake_mb_t *mb = blk;
    return (mb && mb->magic == FAKE_MB_MAGIC) ? mb : NULL;
}

static void mb_free(MB_BLK blk)
{
    fake_mb_t *mb = mb_get(blk);
    if (mb) {
        mb->magic = 0;
        free(mb);
    }
}

// ---------------------------------------------------------------------------
// Sockets
// ---------------------------------------------------------------------------

static int sock_connect(const char *path)
{
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct sockaddr_un addr = { .sun_family = AF_UNIX };
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

// Like sock_connect, but keeps retrying until timeout_ms has passed. At boot
// unisrv can start before capture-v4l2-raw-mpp has created its sockets.
static int sock_connect_wait(const char *path, int timeout_ms)
{
    uint64_t deadline = now_us() + (uint64_t)(timeout_ms > 0 ? timeout_ms : 0) * 1000;
    for (;;) {
        int fd = sock_connect(path);
        if (fd >= 0 || now_us() >= deadline) return fd;
        usleep(100 * 1000);
    }
}

// Read up to `cap` bytes. Stops at EOF, when `exact` bytes were read (if non
// zero), or on timeout. Returns bytes read, or -1 on error/timeout with no data.
static ssize_t sock_read(int fd, uint8_t *buf, size_t cap, size_t exact, int timeout_ms)
{
    size_t got = 0;
    uint64_t deadline = now_us() + (uint64_t)(timeout_ms > 0 ? timeout_ms : 2000) * 1000;
    while (got < cap && (!exact || got < exact)) {
        int64_t left = (int64_t)(deadline - now_us()) / 1000;
        if (left <= 0) break;
        struct pollfd p = { .fd = fd, .events = POLLIN };
        int r = poll(&p, 1, (int)left);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        ssize_t n = read(fd, buf + got, (exact ? exact : cap) - got);
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (n <= 0) break;
        got += (size_t)n;
    }
    return got > 0 ? (ssize_t)got : -1;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

#define MAX_DEV 4
#define MAX_CHN 8
#define MAX_VENC 16

typedef struct {
    bool configured;
    bool enabled;
    uint8_t attr[VI_DEV_ATTR_SIZE];
} vi_dev_t;

typedef struct {
    bool configured;
    RK_U32 width;
    RK_U32 height;
    RK_S32 pixfmt;
} vi_chn_t;

typedef struct {
    bool created;
    RK_S32 codec;
    RK_U32 width;
    RK_U32 height;
    RK_U32 seq;
    // H.264 stream state
    int h264_fd;
    uint8_t *buf;
    size_t len;
    size_t cap;
    bool got_key;
} venc_chn_t;

static vi_dev_t g_vi_dev[MAX_DEV];
static vi_chn_t g_vi_chn[MAX_DEV][MAX_CHN];
static venc_chn_t g_venc[MAX_VENC];
static uint8_t g_jpeg_param[200];
static bool g_jpeg_param_set;

static venc_chn_t *venc_get(RK_S32 chn)
{
    return (chn >= 0 && chn < MAX_VENC) ? &g_venc[chn] : NULL;
}

static void venc_reset_stream(venc_chn_t *v)
{
    if (v->h264_fd >= 0) close(v->h264_fd);
    v->h264_fd = -1;
    free(v->buf);
    v->buf = NULL;
    v->len = v->cap = 0;
    v->got_key = false;
}

static const char *codec_name(RK_S32 c)
{
    switch (c) {
    case RK_VIDEO_ID_AVC: return "H264";
    case RK_VIDEO_ID_JPEG: return "JPEG";
    case RK_VIDEO_ID_MJPEG: return "MJPEG";
    default: return "other";
    }
}

// ---------------------------------------------------------------------------
// SYS
// ---------------------------------------------------------------------------

RK_S32 RK_MPI_SYS_Init(void)
{
    if (!active()) { REAL(RK_S32, RK_MPI_SYS_Init, void); return real_fn ? real_fn() : RK_FAILURE; }
    IMP_INF("SYS_Init");
    return RK_SUCCESS;
}

RK_S32 RK_MPI_SYS_Exit(void)
{
    if (!active()) { REAL(RK_S32, RK_MPI_SYS_Exit, void); return real_fn ? real_fn() : RK_FAILURE; }
    IMP_INF("SYS_Exit");
    return RK_SUCCESS;
}

RK_S32 RK_MPI_SYS_Bind(const MPP_CHN_S *src, const MPP_CHN_S *dst)
{
    if (!active()) { REAL(RK_S32, RK_MPI_SYS_Bind, const MPP_CHN_S *, const MPP_CHN_S *); return real_fn ? real_fn(src, dst) : RK_FAILURE; }
    IMP_DBG("SYS_Bind mod%d:%d:%d -> mod%d:%d:%d",
            src ? src->enModId : -1, src ? src->s32DevId : -1, src ? src->s32ChnId : -1,
            dst ? dst->enModId : -1, dst ? dst->s32DevId : -1, dst ? dst->s32ChnId : -1);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_SYS_UnBind(const MPP_CHN_S *src, const MPP_CHN_S *dst)
{
    if (!active()) { REAL(RK_S32, RK_MPI_SYS_UnBind, const MPP_CHN_S *, const MPP_CHN_S *); return real_fn ? real_fn(src, dst) : RK_FAILURE; }
    IMP_DBG("SYS_UnBind");
    return RK_SUCCESS;
}

// ---------------------------------------------------------------------------
// VI (camera input)
// ---------------------------------------------------------------------------

RK_S32 RK_MPI_VI_SetDevAttr(RK_S32 dev, const void *attr)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_SetDevAttr, RK_S32, const void *); return real_fn ? real_fn(dev, attr) : RK_FAILURE; }
    if (dev < 0 || dev >= MAX_DEV) return RK_FAILURE;
    pthread_mutex_lock(&g_lock);
    if (attr) memcpy(g_vi_dev[dev].attr, attr, VI_DEV_ATTR_SIZE);
    g_vi_dev[dev].configured = true;
    pthread_mutex_unlock(&g_lock);
    IMP_INF("VI_SetDevAttr dev=%d", dev);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_GetDevAttr(RK_S32 dev, void *attr)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_GetDevAttr, RK_S32, void *); return real_fn ? real_fn(dev, attr) : RK_FAILURE; }
    if (dev < 0 || dev >= MAX_DEV) return RK_FAILURE;
    RK_S32 ret = RK_ERR_VI_NOT_CONFIG;
    pthread_mutex_lock(&g_lock);
    if (g_vi_dev[dev].configured) {
        if (attr) memcpy(attr, g_vi_dev[dev].attr, VI_DEV_ATTR_SIZE);
        ret = RK_SUCCESS;
    }
    pthread_mutex_unlock(&g_lock);
    IMP_DBG("VI_GetDevAttr dev=%d -> 0x%x", dev, (unsigned)ret);
    return ret;
}

RK_S32 RK_MPI_VI_EnableDev(RK_S32 dev)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_EnableDev, RK_S32); return real_fn ? real_fn(dev) : RK_FAILURE; }
    if (dev < 0 || dev >= MAX_DEV) return RK_FAILURE;
    g_vi_dev[dev].enabled = true;
    IMP_INF("VI_EnableDev dev=%d", dev);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_DisableDev(RK_S32 dev)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_DisableDev, RK_S32); return real_fn ? real_fn(dev) : RK_FAILURE; }
    if (dev < 0 || dev >= MAX_DEV) return RK_FAILURE;
    g_vi_dev[dev].enabled = false;
    IMP_INF("VI_DisableDev dev=%d", dev);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_GetDevIsEnable(RK_S32 dev)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_GetDevIsEnable, RK_S32); return real_fn ? real_fn(dev) : RK_FAILURE; }
    if (dev < 0 || dev >= MAX_DEV) return RK_FAILURE;
    // unisrv: 0 = already enabled, non-zero = needs EnableDev
    return g_vi_dev[dev].enabled ? RK_SUCCESS : RK_FAILURE;
}

RK_S32 RK_MPI_VI_SetDevBindPipe(RK_S32 dev, const void *bind)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_SetDevBindPipe, RK_S32, const void *); return real_fn ? real_fn(dev, bind) : RK_FAILURE; }
    IMP_DBG("VI_SetDevBindPipe dev=%d", dev);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_SetChnAttr(RK_S32 pipe, RK_S32 chn, const void *attr)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_SetChnAttr, RK_S32, RK_S32, const void *); return real_fn ? real_fn(pipe, chn, attr) : RK_FAILURE; }
    if (pipe < 0 || pipe >= MAX_DEV || chn < 0 || chn >= MAX_CHN || !attr) return RK_FAILURE;
    const uint8_t *a = attr;
    vi_chn_t *c = &g_vi_chn[pipe][chn];
    memcpy(&c->width, a + 0, 4);
    memcpy(&c->height, a + 4, 4);
    memcpy(&c->pixfmt, a + VI_CHN_ATTR_PIXFMT_OFFSET, 4);
    c->configured = true;
    char entity[VI_CHN_ATTR_ENTITY_SIZE + 1] = { 0 };
    memcpy(entity, a + VI_CHN_ATTR_ENTITY_OFFSET, VI_CHN_ATTR_ENTITY_SIZE);
    for (int i = 0; i < VI_CHN_ATTR_ENTITY_SIZE && entity[i]; i++)
        if (entity[i] < 0x20 || entity[i] > 0x7e) { entity[i] = '\0'; break; }
    IMP_INF("VI_SetChnAttr pipe=%d chn=%d %ux%u fmt=0x%x entity=%s",
            pipe, chn, c->width, c->height, (unsigned)c->pixfmt, entity);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_EnableChn(RK_S32 pipe, RK_S32 chn)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_EnableChn, RK_S32, RK_S32); return real_fn ? real_fn(pipe, chn) : RK_FAILURE; }
    IMP_INF("VI_EnableChn pipe=%d chn=%d", pipe, chn);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_DisableChn(RK_S32 pipe, RK_S32 chn)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_DisableChn, RK_S32, RK_S32); return real_fn ? real_fn(pipe, chn) : RK_FAILURE; }
    IMP_INF("VI_DisableChn pipe=%d chn=%d", pipe, chn);
    return RK_SUCCESS;
}

// Nearest-neighbour scale of an NV12 frame into NV12 or I420.
static void convert_nv12(const uint8_t *src, RK_U32 sw, RK_U32 sh,
                         uint8_t *dst, RK_U32 dw, RK_U32 dh, RK_S32 fmt)
{
    const uint8_t *suv = src + (size_t)sw * sh;
    uint8_t *dy = dst;
    for (RK_U32 y = 0; y < dh; y++) {
        const uint8_t *srow = src + (size_t)(y * sh / dh) * sw;
        uint8_t *drow = dy + (size_t)y * dw;
        if (dw == sw) {
            memcpy(drow, srow, dw);
        } else {
            for (RK_U32 x = 0; x < dw; x++) drow[x] = srow[x * sw / dw];
        }
    }
    RK_U32 cw = dw / 2, ch = dh / 2, scw = sw / 2, sch = sh / 2;
    if (fmt == RK_FMT_YUV420P) {
        uint8_t *du = dst + (size_t)dw * dh;
        uint8_t *dv = du + (size_t)cw * ch;
        for (RK_U32 y = 0; y < ch; y++) {
            const uint8_t *srow = suv + (size_t)(y * sch / ch) * sw;
            for (RK_U32 x = 0; x < cw; x++) {
                RK_U32 sx = x * scw / cw;
                du[(size_t)y * cw + x] = srow[sx * 2];
                dv[(size_t)y * cw + x] = srow[sx * 2 + 1];
            }
        }
    } else {
        uint8_t *duv = dst + (size_t)dw * dh;
        for (RK_U32 y = 0; y < ch; y++) {
            const uint8_t *srow = suv + (size_t)(y * sch / ch) * sw;
            uint8_t *drow = duv + (size_t)y * dw;
            if (dw == sw) {
                memcpy(drow, srow, dw);
            } else {
                for (RK_U32 x = 0; x < cw; x++) {
                    RK_U32 sx = x * scw / cw;
                    drow[x * 2] = srow[sx * 2];
                    drow[x * 2 + 1] = srow[sx * 2 + 1];
                }
            }
        }
    }
}

RK_S32 RK_MPI_VI_GetChnFrame(RK_S32 pipe, RK_S32 chn, VIDEO_FRAME_INFO_S *frame, RK_S32 ms)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_GetChnFrame, RK_S32, RK_S32, VIDEO_FRAME_INFO_S *, RK_S32); return real_fn ? real_fn(pipe, chn, frame, ms) : RK_FAILURE; }
    if (!frame || pipe < 0 || pipe >= MAX_DEV || chn < 0 || chn >= MAX_CHN) return RK_FAILURE;

    vi_chn_t c = g_vi_chn[pipe][chn];
    RK_U32 dw = c.width ? c.width : g_raw_width;
    RK_U32 dh = c.height ? c.height : g_raw_height;
    RK_S32 fmt = c.pixfmt;
    if (fmt != RK_FMT_YUV420SP && fmt != RK_FMT_YUV420P) {
        IMP_ERR("VI_GetChnFrame pipe=%d chn=%d: unsupported pixel format 0x%x", pipe, chn, (unsigned)fmt);
        return RK_FAILURE;
    }
    dw &= ~1u;
    dh &= ~1u;

    size_t raw_size = (size_t)g_raw_width * g_raw_height * 3 / 2;
    uint8_t *raw = malloc(raw_size);
    if (!raw) return RK_FAILURE;

    int fd = sock_connect_wait(g_raw_sock, ms);
    ssize_t n = fd >= 0 ? sock_read(fd, raw, raw_size, raw_size, ms) : -1;
    if (fd >= 0) close(fd);
    if (n != (ssize_t)raw_size) {
        IMP_ERR("VI_GetChnFrame pipe=%d chn=%d: raw frame read failed (%zd of %zu bytes)", pipe, chn, n, raw_size);
        free(raw);
        return RK_FAILURE;
    }

    size_t out_size = (size_t)dw * dh * 3 / 2;
    fake_mb_t *mb = mb_alloc(out_size);
    if (!mb) {
        free(raw);
        return RK_FAILURE;
    }
    convert_nv12(raw, g_raw_width, g_raw_height, mb->data, dw, dh, fmt);
    free(raw);

    memset(frame, 0, sizeof(*frame));
    frame->pMbBlk = mb;
    frame->u32Width = dw;
    frame->u32Height = dh;
    frame->u32VirWidth = dw;
    frame->u32VirHeight = dh;
    frame->enPixelFormat = fmt;
    frame->pVirAddr[0] = mb->data;
    frame->pVirAddr[1] = mb->data + (size_t)dw * dh;
    frame->u64PTS = now_us();
    IMP_DBG("VI_GetChnFrame pipe=%d chn=%d -> %ux%u fmt=0x%x", pipe, chn, dw, dh, (unsigned)fmt);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VI_ReleaseChnFrame(RK_S32 pipe, RK_S32 chn, const VIDEO_FRAME_INFO_S *frame)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VI_ReleaseChnFrame, RK_S32, RK_S32, const VIDEO_FRAME_INFO_S *); return real_fn ? real_fn(pipe, chn, frame) : RK_FAILURE; }
    if (frame) mb_free(frame->pMbBlk);
    return RK_SUCCESS;
}

// ---------------------------------------------------------------------------
// VENC (encoder)
// ---------------------------------------------------------------------------

RK_S32 RK_MPI_VENC_CreateChn(RK_S32 chn, const void *attr)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_CreateChn, RK_S32, const void *); return real_fn ? real_fn(chn, attr) : RK_FAILURE; }
    venc_chn_t *v = venc_get(chn);
    if (!v || !attr) return RK_FAILURE;
    pthread_mutex_lock(&g_lock);
    venc_reset_stream(v);
    const uint8_t *a = attr;
    memcpy(&v->codec, a, 4);
    memcpy(&v->width, a + VENC_CHN_ATTR_WIDTH_OFFSET, 4);
    memcpy(&v->height, a + VENC_CHN_ATTR_HEIGHT_OFFSET, 4);
    v->created = true;
    v->seq = 0;
    pthread_mutex_unlock(&g_lock);
    IMP_INF("VENC_CreateChn chn=%d codec=%s(%d) %ux%u", chn, codec_name(v->codec), v->codec, v->width, v->height);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_DestroyChn(RK_S32 chn)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_DestroyChn, RK_S32); return real_fn ? real_fn(chn) : RK_FAILURE; }
    venc_chn_t *v = venc_get(chn);
    if (!v) return RK_FAILURE;
    pthread_mutex_lock(&g_lock);
    venc_reset_stream(v);
    v->created = false;
    pthread_mutex_unlock(&g_lock);
    IMP_INF("VENC_DestroyChn chn=%d", chn);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_SetModParam(const void *param)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_SetModParam, const void *); return real_fn ? real_fn(param) : RK_FAILURE; }
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_GetJpegParam(RK_S32 chn, void *param)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_GetJpegParam, RK_S32, void *); return real_fn ? real_fn(chn, param) : RK_FAILURE; }
    if (param) memcpy(param, g_jpeg_param, sizeof(g_jpeg_param));
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_SetJpegParam(RK_S32 chn, const void *param)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_SetJpegParam, RK_S32, const void *); return real_fn ? real_fn(chn, param) : RK_FAILURE; }
    if (param) {
        memcpy(g_jpeg_param, param, sizeof(g_jpeg_param));
        g_jpeg_param_set = true;
    }
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_StartRecvFrame(RK_S32 chn, const void *param)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_StartRecvFrame, RK_S32, const void *); return real_fn ? real_fn(chn, param) : RK_FAILURE; }
    RK_S32 num = 0;
    if (param) memcpy(&num, param, 4);
    IMP_DBG("VENC_StartRecvFrame chn=%d frames=%d", chn, num);
    return RK_SUCCESS;
}

RK_S32 RK_MPI_VENC_StopRecvFrame(RK_S32 chn)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_StopRecvFrame, RK_S32); return real_fn ? real_fn(chn) : RK_FAILURE; }
    IMP_DBG("VENC_StopRecvFrame chn=%d", chn);
    return RK_SUCCESS;
}

static void fill_stream(void *stream, venc_chn_t *v, fake_mb_t *mb, RK_S32 type)
{
    VENC_STREAM_HEAD_S *s = stream;
    VENC_PACK_S *p = s->pstPack;
    memset(p, 0, sizeof(*p));
    p->pMbBlk = mb;
    p->u32Len = (RK_U32)mb->size;
    p->u64PTS = now_us();
    p->bFrameEnd = 1;
    p->DataType = type;
    p->u32DataNum = 1;
    p->stPackInfo[0].u32PackType = type;
    p->stPackInfo[0].u32PackLength = (RK_U32)mb->size;
    s->u32PackCount = 1;
    s->u32Seq = v->seq++;
}

static RK_S32 get_jpeg(RK_S32 chn, venc_chn_t *v, void *stream, RK_S32 ms)
{
    const size_t cap = 4 * 1024 * 1024;
    uint8_t *buf = malloc(cap);
    if (!buf) return RK_FAILURE;
    int fd = sock_connect_wait(g_jpeg_sock, ms > 0 ? ms : 2000);
    ssize_t n = fd >= 0 ? sock_read(fd, buf, cap, 0, ms > 0 ? ms : 2000) : -1;
    if (fd >= 0) close(fd);
    if (n < 4 || buf[0] != 0xff || buf[1] != 0xd8) {
        IMP_ERR("VENC_GetStream chn=%d: no JPEG from %s (%zd bytes)", chn, g_jpeg_sock, n);
        free(buf);
        return RK_FAILURE;
    }
    fake_mb_t *mb = mb_alloc((size_t)n);
    if (!mb) {
        free(buf);
        return RK_FAILURE;
    }
    memcpy(mb->data, buf, (size_t)n);
    free(buf);
    fill_stream(stream, v, mb, JPEGE_PACK_PIC);
    IMP_INF("VENC_GetStream chn=%d JPEG %zd bytes", chn, n);
    return RK_SUCCESS;
}

// H.264 access unit splitting, as in v4l2-mpp's h264_frames.h.
static const uint8_t *h264_find_nal(const uint8_t *d, size_t size)
{
    for (size_t i = 0; i + 3 < size; i++)
        if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 0 && d[i + 3] == 1 && size - i > 4)
            return d + i;
    return NULL;
}

static bool h264_is_slice(const uint8_t *nal, size_t size)
{
    uint8_t t = size >= 5 ? (nal[4] & 0x1f) : 0;
    return t == 1 || t == 5;
}

// True if this NAL can only begin a new access unit once a slice has been
// seen: AUD, SEI, SPS, PPS, or a slice with first_mb_in_slice == 0.
static bool h264_starts_au(const uint8_t *nal, size_t size)
{
    if (size < 5) return false;
    uint8_t t = nal[4] & 0x1f;
    if (t == 6 || t == 7 || t == 8 || t == 9) return true;
    if (t == 1 || t == 5) return size < 6 || (nal[5] & 0x80) != 0;
    return false;
}

// Finds one complete access unit in buf. Returns its start/length, or false if
// more data is needed.
static bool h264_next_frame(const uint8_t *buf, size_t len, size_t *off, size_t *flen)
{
    const uint8_t *end = buf + len;
    const uint8_t *start = h264_find_nal(buf, len);
    if (!start) return false;
    const uint8_t *next = start;
    bool found_slice = h264_is_slice(start, (size_t)(end - start));
    while ((next = h264_find_nal(next + 4, (size_t)(end - next - 4))) != NULL) {
        size_t left = (size_t)(end - next);
        if (found_slice && h264_starts_au(next, left)) break;
        if (h264_is_slice(next, left)) found_slice = true;
    }
    if (!next) return false;
    *off = (size_t)(start - buf);
    *flen = (size_t)(next - start);
    return true;
}

static bool h264_has_nal(const uint8_t *d, size_t size, uint8_t type)
{
    for (size_t i = 0; i + 4 < size; i++)
        if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1 && (d[i + 3] & 0x1f) == type)
            return true;
    return false;
}

static RK_S32 get_h264(RK_S32 chn, venc_chn_t *v, void *stream, RK_S32 ms)
{
    uint64_t deadline = now_us() + (uint64_t)(ms > 0 ? ms : 1000) * 1000;
    for (;;) {
        if (v->h264_fd < 0) {
            v->h264_fd = sock_connect(g_h264_sock);
            if (v->h264_fd < 0) {
                IMP_ERR("VENC_GetStream chn=%d: cannot connect to %s", chn, g_h264_sock);
                return RK_FAILURE;
            }
            IMP_INF("VENC_GetStream chn=%d: connected to %s", chn, g_h264_sock);
        }

        size_t off, flen;
        while (v->buf && h264_next_frame(v->buf, v->len, &off, &flen)) {
            const uint8_t *f = v->buf + off;
            bool idr = h264_has_nal(f, flen, 5);
            bool key = idr || h264_has_nal(f, flen, 7);
            if (key) v->got_key = true;
            if (v->got_key) {
                fake_mb_t *mb = mb_alloc(flen);
                if (!mb) return RK_FAILURE;
                memcpy(mb->data, f, flen);
                size_t used = off + flen;
                memmove(v->buf, v->buf + used, v->len - used);
                v->len -= used;
                fill_stream(stream, v, mb, idr ? H264E_NALU_IDRSLICE : H264E_NALU_PSLICE);
                if (v->seq <= 3 || v->seq % 300 == 0)
                    IMP_INF("VENC_GetStream chn=%d H264 frame #%u %zu bytes%s", chn, v->seq, flen, key ? " (key)" : "");
                return RK_SUCCESS;
            }
            size_t used = off + flen; // drop frames until the first keyframe
            memmove(v->buf, v->buf + used, v->len - used);
            v->len -= used;
        }

        int64_t left = (int64_t)(deadline - now_us()) / 1000;
        if (left <= 0) return RK_FAILURE;
        if (v->cap - v->len < 64 * 1024) {
            size_t ncap = v->cap ? v->cap * 2 : 512 * 1024;
            if (ncap > 8 * 1024 * 1024) { // runaway buffer: start over
                v->len = 0;
                ncap = v->cap;
            }
            uint8_t *nb = ncap != v->cap ? realloc(v->buf, ncap) : v->buf;
            if (!nb) return RK_FAILURE;
            v->buf = nb;
            v->cap = ncap;
        }
        struct pollfd p = { .fd = v->h264_fd, .events = POLLIN };
        int r = poll(&p, 1, (int)left);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return RK_FAILURE;
        ssize_t n = read(v->h264_fd, v->buf + v->len, v->cap - v->len);
        if (n <= 0) {
            IMP_ERR("VENC_GetStream chn=%d: H264 socket closed", chn);
            venc_reset_stream(v);
            return RK_FAILURE;
        }
        v->len += (size_t)n;
    }
}

RK_S32 RK_MPI_VENC_GetStream(RK_S32 chn, void *stream, RK_S32 ms)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_GetStream, RK_S32, void *, RK_S32); return real_fn ? real_fn(chn, stream, ms) : RK_FAILURE; }
    venc_chn_t *v = venc_get(chn);
    if (!v || !v->created || !stream || !((VENC_STREAM_HEAD_S *)stream)->pstPack) return RK_FAILURE;
    switch (v->codec) {
    case RK_VIDEO_ID_JPEG:
    case RK_VIDEO_ID_MJPEG:
        return get_jpeg(chn, v, stream, ms);
    case RK_VIDEO_ID_AVC:
        return get_h264(chn, v, stream, ms);
    default:
        IMP_ERR("VENC_GetStream chn=%d: unsupported codec %d", chn, v->codec);
        return RK_FAILURE;
    }
}

RK_S32 RK_MPI_VENC_ReleaseStream(RK_S32 chn, void *stream)
{
    if (!active()) { REAL(RK_S32, RK_MPI_VENC_ReleaseStream, RK_S32, void *); return real_fn ? real_fn(chn, stream) : RK_FAILURE; }
    VENC_STREAM_HEAD_S *s = stream;
    if (s && s->pstPack) {
        for (RK_U32 i = 0; i < s->u32PackCount && i < 8; i++) {
            mb_free(s->pstPack[i].pMbBlk);
            s->pstPack[i].pMbBlk = NULL;
        }
    }
    return RK_SUCCESS;
}

// ---------------------------------------------------------------------------
// MB
// ---------------------------------------------------------------------------

void *RK_MPI_MB_Handle2VirAddr(MB_BLK blk)
{
    fake_mb_t *mb = mb_get(blk);
    if (mb) return mb->data;
    REAL(void *, RK_MPI_MB_Handle2VirAddr, MB_BLK);
    return real_fn ? real_fn(blk) : NULL;
}
