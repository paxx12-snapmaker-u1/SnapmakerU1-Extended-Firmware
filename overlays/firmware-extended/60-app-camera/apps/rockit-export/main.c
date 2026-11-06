// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12 @RrrrobertB1992

// LD_PRELOAD into stock 2.1.0 unisrv. unisrv keeps owning the internal camera
// through librockit exactly as on stock firmware. This library only hooks
// RK_MPI_VI_EnableChn: once unisrv has enabled the export channel (by default
// VI pipe 0 channel 2, the ISP selfpath, which unisrv only reads 3 frames
// from at startup), a thread reads its frames, encodes them with its own
// Rockit VENC channels and serves the same sockets capture-v4l2-raw-mpp does:
//
//   ROCKIT_EXPORT_H264_SOCKET   H.264 Annex-B stream, AUD after each frame,
//                               keyframe for every new client
//   ROCKIT_EXPORT_JPEG_SOCKET   one JPEG per connection, then close
//   ROCKIT_EXPORT_MJPEG_SOCKET  one JPEG per frame, dropped for slow clients
//
// Encoders only run while a socket has clients. Sockets and logging come from
// v4l2-mpp's common headers, so behaviour matches capture-v4l2-raw-mpp. Logs go
// to unisrv's stdout and stderr.

#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "callback_chain.h"
#include "h264_frames.h"
#include "log.h"
#include "sock_ctx.h"

// ---------------------------------------------------------------------------
// Minimal Rockit MPI ABI (aarch64), checked against the Rockit SDK headers
// and against how stock 2.1.0 unisrv fills these structures.
// ---------------------------------------------------------------------------

typedef int32_t RK_S32;
typedef uint8_t RK_U8;
typedef uint32_t RK_U32;
typedef uint64_t RK_U64;
typedef RK_S32 RK_BOOL;
typedef void *MB_BLK;

typedef struct {
    RK_U32 u32Width;
    RK_U32 u32Height;
} SIZE_S;

typedef struct {
    RK_S32 s32X;
    RK_S32 s32Y;
    RK_U32 u32Width;
    RK_U32 u32Height;
} RECT_S;

typedef struct {
    RK_S32 s32SrcFrameRate;
    RK_S32 s32DstFrameRate;
} FRAME_RATE_CTRL_S;

typedef struct {
    RK_U32 u32BufCount;
    RK_U32 u32BufSize;
    RK_S32 enCaptureType;
    RK_S32 enMemoryType;
    char aEntityName[32];
    RK_BOOL bNoUseLibV4L2;
    SIZE_S stMaxSize;
    RECT_S stWindow;
} VI_ISP_OPT_S;

typedef struct {
    RK_S32 enModId;
    RK_S32 s32DevId;
    RK_S32 s32ChnId;
} MPP_CHN_S;

typedef struct {
    SIZE_S stSize;
    RK_S32 enPixelFormat;
    RK_S32 enDynamicRange;
    RK_S32 enVideoFormat;
    RK_S32 enCompressMode;
    RK_BOOL bMirror;
    RK_BOOL bFlip;
    RK_U32 u32Depth;
    FRAME_RATE_CTRL_S stFrameRate;
    RK_S32 enAllocBufType;
    VI_ISP_OPT_S stIspOpt;
    MPP_CHN_S stShareBufChn;
} VI_CHN_ATTR_S;

typedef struct {
    MB_BLK pMbBlk;
    RK_U32 u32Width, u32Height, u32VirWidth, u32VirHeight;
    RK_S32 enField, enPixelFormat, enVideoFormat, enCompressMode, enDynamicRange, enColorGamut;
    void *pVirAddr[2];
    RK_U32 u32TimeRef;
    RK_U64 u64PTS, u64PrivateData;
    RK_U32 u32FrameFlag;
} VIDEO_FRAME_INFO_S;

typedef struct {
    MB_BLK pMbBlk;
    RK_U32 u32Len;
    RK_U64 u64PTS;
    RK_S32 bFrameEnd, bStreamEnd, DataType;
    RK_U32 u32Offset, u32DataNum;
    uint8_t stPackInfo[96];
} VENC_PACK_S;

typedef struct {
    VENC_PACK_S *pstPack;
    uint8_t reserved0[8];
    RK_U32 u32PackCount, u32Seq;
    uint8_t rest[400 - 24];
} VENC_STREAM_S;

_Static_assert(sizeof(VIDEO_FRAME_INFO_S) == 96, "VIDEO_FRAME_INFO_S");
_Static_assert(sizeof(VENC_PACK_S) == 144, "VENC_PACK_S");
_Static_assert(offsetof(VENC_PACK_S, u32Offset) == 36, "u32Offset");
_Static_assert(sizeof(VENC_STREAM_S) == 400, "VENC_STREAM_S");
_Static_assert(offsetof(VENC_STREAM_S, u32PackCount) == 16, "u32PackCount");

#define RK_VIDEO_ID_AVC 8
#define RK_VIDEO_ID_JPEG 15
#define RK_FMT_YUV420SP 0
#define VENC_RC_MODE_H264CBR 1
#define VENC_GOPMODE_NORMALP 1

typedef struct {
    RK_U8 u8LargeThumbNailNum;
    SIZE_S astLargeThumbNailSize[2];
} VENC_MPF_CFG_S;

typedef struct {
    RK_BOOL bSupportDCF;
    VENC_MPF_CFG_S stMPFCfg;
    RK_S32 enReceiveMode;
} VENC_ATTR_JPEG_S;

typedef struct {
    RK_U32 u32Level;
} VENC_ATTR_H264_S;

typedef struct {
    RK_S32 enType;
    RK_U32 u32MaxPicWidth;
    RK_U32 u32MaxPicHeight;
    RK_S32 enPixelFormat;
    RK_S32 enMirror;
    RK_U32 u32BufSize;
    RK_U32 u32Profile;
    RK_BOOL bByFrame;
    RK_U32 u32PicWidth;
    RK_U32 u32PicHeight;
    RK_U32 u32VirWidth;
    RK_U32 u32VirHeight;
    RK_U32 u32StreamBufCnt;
    union {
        VENC_ATTR_H264_S stAttrH264e;
        VENC_ATTR_JPEG_S stAttrJpege;
    };
} VENC_ATTR_S;

typedef struct {
    RK_U32 u32Gop;
    RK_U32 u32SrcFrameRateNum;
    RK_U32 u32SrcFrameRateDen;
    RK_U32 fr32DstFrameRateNum;
    RK_U32 fr32DstFrameRateDen;
    RK_U32 u32BitRate;
    RK_U32 u32StatTime;
} VENC_H264_CBR_S;

typedef struct {
    RK_S32 enRcMode;
    union {
        VENC_H264_CBR_S stH264Cbr;
        uint8_t reserved[36];
    };
} VENC_RC_ATTR_S;

typedef struct {
    RK_S32 enGopMode;
    RK_S32 s32VirIdrLen;
    RK_U32 u32MaxLtrCount;
    RK_U32 u32TsvcPreload;
} VENC_GOP_ATTR_S;

typedef struct {
    VENC_ATTR_S stVencAttr;
    VENC_RC_ATTR_S stRcAttr;
    VENC_GOP_ATTR_S stGopAttr;
} VENC_CHN_ATTR_S;

typedef struct {
    RK_U32 u32Qfactor;
    RK_U8 u8YQt[64];
    RK_U8 u8CbQt[64];
    RK_U8 u8CrQt[64];
    RK_U32 u32MCUPerECS;
} VENC_JPEG_PARAM_S;

_Static_assert(sizeof(VI_ISP_OPT_S) == 76, "VI_ISP_OPT_S");
_Static_assert(offsetof(VI_CHN_ATTR_S, stFrameRate) == 36, "VI_CHN_ATTR_S.stFrameRate");
_Static_assert(sizeof(VI_CHN_ATTR_S) == 136, "VI_CHN_ATTR_S");
_Static_assert(sizeof(VENC_MPF_CFG_S) == 20, "VENC_MPF_CFG_S");
_Static_assert(sizeof(VENC_ATTR_JPEG_S) == 28, "VENC_ATTR_JPEG_S");
_Static_assert(sizeof(VENC_ATTR_S) == 80, "VENC_ATTR_S");
_Static_assert(offsetof(VENC_CHN_ATTR_S, stRcAttr) == 80, "VENC_CHN_ATTR_S.stRcAttr");
_Static_assert(offsetof(VENC_CHN_ATTR_S, stGopAttr) == 120, "VENC_CHN_ATTR_S.stGopAttr");
_Static_assert(sizeof(VENC_CHN_ATTR_S) == 136, "VENC_CHN_ATTR_S");
_Static_assert(sizeof(VENC_JPEG_PARAM_S) == 200, "VENC_JPEG_PARAM_S");

// ---------------------------------------------------------------------------
// Config and logging
// ---------------------------------------------------------------------------

static struct {
    char h264_sock[108], jpeg_sock[108], mjpeg_sock[108];
    int pipe, chn, venc_h264, venc_jpeg;
    int fps, bitrate_kbps, jpeg_qfactor;
} cfg = {
    .h264_sock = "/tmp/capture-mipi-h264.sock",
    .jpeg_sock = "/tmp/capture-mipi-jpeg.sock",
    .mjpeg_sock = "/tmp/capture-mipi-mjpeg.sock",
    .pipe = 0, .chn = 2, .venc_h264 = 6, .venc_jpeg = 7,
    .fps = 30, .bitrate_kbps = 2000, .jpeg_qfactor = 77,
};

static bool g_active;
static pthread_once_t g_once = PTHREAD_ONCE_INIT;

#define LOGI(fmt, ...) log_printf("rockit-export: " fmt "\n", ##__VA_ARGS__)
#define LOGE(fmt, ...) log_errorf("rockit-export: " fmt "\n", ##__VA_ARGS__)

static void env_str(const char *name, char *dst, size_t n)
{
    const char *v = getenv(name);
    if (v && *v) { strncpy(dst, v, n - 1); dst[n - 1] = 0; }
}

static void env_int(const char *name, int *dst)
{
    const char *v = getenv(name);
    if (v && *v) *dst = atoi(v);
}

static void init_once(void)
{
    char exe[256];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (n <= 0) return;
    exe[n] = 0;
    const char *base = strrchr(exe, '/');
    base = base ? base + 1 : exe;
    if (strcmp(base, "unisrv") != 0) return;

    env_str("ROCKIT_EXPORT_H264_SOCKET", cfg.h264_sock, sizeof(cfg.h264_sock));
    env_str("ROCKIT_EXPORT_JPEG_SOCKET", cfg.jpeg_sock, sizeof(cfg.jpeg_sock));
    env_str("ROCKIT_EXPORT_MJPEG_SOCKET", cfg.mjpeg_sock, sizeof(cfg.mjpeg_sock));
    env_int("ROCKIT_EXPORT_VI_PIPE", &cfg.pipe);
    env_int("ROCKIT_EXPORT_VI_CHN", &cfg.chn);
    env_int("ROCKIT_EXPORT_VENC_H264", &cfg.venc_h264);
    env_int("ROCKIT_EXPORT_VENC_JPEG", &cfg.venc_jpeg);
    env_int("ROCKIT_EXPORT_FPS", &cfg.fps);
    env_int("ROCKIT_EXPORT_H264_BITRATE", &cfg.bitrate_kbps);
    env_int("ROCKIT_EXPORT_JPEG_QFACTOR", &cfg.jpeg_qfactor);
    if (cfg.fps <= 0) cfg.fps = 30;
    assert(cfg.venc_h264 != cfg.venc_jpeg);

    g_active = true;
    LOGI("active in unisrv: VI %d:%d, VENC h264=%d jpeg=%d, "
         "%d fps, %d kbps, jpeg q%d",
         cfg.pipe, cfg.chn, cfg.venc_h264, cfg.venc_jpeg, cfg.fps,
         cfg.bitrate_kbps, cfg.jpeg_qfactor);
}

static uint64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

// ---------------------------------------------------------------------------
// Real librockit functions
// ---------------------------------------------------------------------------

static struct {
    RK_S32 (*vi_enable_chn)(RK_S32, RK_S32);
    RK_S32 (*vi_get_attr)(RK_S32, RK_S32, VI_CHN_ATTR_S *);
    RK_S32 (*vi_set_attr)(RK_S32, RK_S32, const VI_CHN_ATTR_S *);
    RK_S32 (*vi_get_frame)(RK_S32, RK_S32, VIDEO_FRAME_INFO_S *, RK_S32);
    RK_S32 (*vi_release_frame)(RK_S32, RK_S32, const VIDEO_FRAME_INFO_S *);
    RK_S32 (*venc_create)(RK_S32, const void *);
    RK_S32 (*venc_destroy)(RK_S32);
    RK_S32 (*venc_start)(RK_S32, const void *);
    RK_S32 (*venc_stop)(RK_S32);
    RK_S32 (*venc_send)(RK_S32, const VIDEO_FRAME_INFO_S *, RK_S32);
    RK_S32 (*venc_get)(RK_S32, VENC_STREAM_S *, RK_S32);
    RK_S32 (*venc_release)(RK_S32, VENC_STREAM_S *);
    RK_S32 (*venc_idr)(RK_S32, RK_S32);
    RK_S32 (*venc_get_jpeg)(RK_S32, void *);
    RK_S32 (*venc_set_jpeg)(RK_S32, const void *);
    void *(*mb_vir)(MB_BLK);
} rk;

static bool g_rockit_loaded;
static pthread_once_t g_rockit_once = PTHREAD_ONCE_INIT;

static void load_rockit_once(void)
{
#define LOAD(field, name) do { \
    if (!(rk.field = dlsym(RTLD_NEXT, name))) { \
        LOGE("missing %s", name); \
        return; \
    } \
} while (0)
    LOAD(vi_enable_chn, "RK_MPI_VI_EnableChn");
    LOAD(vi_get_attr, "RK_MPI_VI_GetChnAttr");
    LOAD(vi_set_attr, "RK_MPI_VI_SetChnAttr");
    LOAD(vi_get_frame, "RK_MPI_VI_GetChnFrame");
    LOAD(vi_release_frame, "RK_MPI_VI_ReleaseChnFrame");
    LOAD(venc_create, "RK_MPI_VENC_CreateChn");
    LOAD(venc_destroy, "RK_MPI_VENC_DestroyChn");
    LOAD(venc_start, "RK_MPI_VENC_StartRecvFrame");
    LOAD(venc_stop, "RK_MPI_VENC_StopRecvFrame");
    LOAD(venc_send, "RK_MPI_VENC_SendFrame");
    LOAD(venc_get, "RK_MPI_VENC_GetStream");
    LOAD(venc_release, "RK_MPI_VENC_ReleaseStream");
    LOAD(venc_idr, "RK_MPI_VENC_RequestIDR");
    LOAD(venc_get_jpeg, "RK_MPI_VENC_GetJpegParam");
    LOAD(venc_set_jpeg, "RK_MPI_VENC_SetJpegParam");
    LOAD(mb_vir, "RK_MPI_MB_Handle2VirAddr");
#undef LOAD
    g_rockit_loaded = true;
}

static bool load_rockit(void)
{
    pthread_once(&g_rockit_once, load_rockit_once);
    return g_rockit_loaded;
}

// ---------------------------------------------------------------------------
// Sockets
// ---------------------------------------------------------------------------

// Room for about two 1080p JPEG frames, so a client that is a little behind
// does not immediately make a write stall (the default is about one frame).
#define SOCK_SNDBUF_SIZE (512 * 1024)

// ---------------------------------------------------------------------------
// Encoders
// ---------------------------------------------------------------------------

static bool g_h264_ready, g_jpeg_ready;
static bool g_warned_multiple_packs, g_warned_missing_idr;

static bool venc_h264_open(RK_U32 w, RK_U32 h)
{
    VENC_CHN_ATTR_S a = { 0 };
    a.stVencAttr.enType = RK_VIDEO_ID_AVC;
    a.stVencAttr.u32MaxPicWidth = w;
    a.stVencAttr.u32MaxPicHeight = h;
    a.stVencAttr.enPixelFormat = RK_FMT_YUV420SP;
    a.stVencAttr.u32BufSize = w * h;
    a.stVencAttr.u32Profile = 100;  // H.264 High
    a.stVencAttr.bByFrame = 1;
    a.stVencAttr.u32PicWidth = w;
    a.stVencAttr.u32PicHeight = h;
    a.stVencAttr.u32VirWidth = w;
    a.stVencAttr.u32VirHeight = h;
    a.stVencAttr.u32StreamBufCnt = 4;
    a.stVencAttr.stAttrH264e.u32Level = 41;
    a.stRcAttr.enRcMode = VENC_RC_MODE_H264CBR;
    a.stRcAttr.stH264Cbr.u32Gop = (RK_U32)cfg.fps * 2;
    a.stRcAttr.stH264Cbr.u32SrcFrameRateNum = (RK_U32)cfg.fps;
    a.stRcAttr.stH264Cbr.u32SrcFrameRateDen = 1;
    a.stRcAttr.stH264Cbr.fr32DstFrameRateNum = (RK_U32)cfg.fps;
    a.stRcAttr.stH264Cbr.fr32DstFrameRateDen = 1;
    a.stRcAttr.stH264Cbr.u32BitRate = (RK_U32)cfg.bitrate_kbps;
    a.stRcAttr.stH264Cbr.u32StatTime = 3;
    a.stGopAttr.enGopMode = VENC_GOPMODE_NORMALP;
    RK_S32 r = rk.venc_create(cfg.venc_h264, &a);
    if (r != 0) {
        LOGE("VENC_CreateChn h264 chn=%d failed: 0x%x", cfg.venc_h264, (unsigned)r);
        return false;
    }
    RK_S32 recv = -1;
    r = rk.venc_start(cfg.venc_h264, &recv);
    if (r != 0) {
        LOGE("VENC_StartRecvFrame h264 failed: 0x%x", (unsigned)r);
        rk.venc_destroy(cfg.venc_h264);
        return false;
    }
    LOGI("H.264 encoder ready on VENC %d: %ux%u, %d fps, %d kbps",
         cfg.venc_h264, w, h, cfg.fps, cfg.bitrate_kbps);
    return true;
}

static bool venc_jpeg_open(RK_U32 w, RK_U32 h)
{
    VENC_CHN_ATTR_S a = { 0 };
    a.stVencAttr.enType = RK_VIDEO_ID_JPEG;
    a.stVencAttr.enPixelFormat = RK_FMT_YUV420SP;
    a.stVencAttr.u32BufSize = w * h * 2;  // same as unisrv's own JPEG channel
    a.stVencAttr.u32PicWidth = w;
    a.stVencAttr.u32PicHeight = h;
    a.stVencAttr.u32VirWidth = w;
    a.stVencAttr.u32VirHeight = h;
    a.stVencAttr.u32StreamBufCnt = 5;
    RK_S32 r = rk.venc_create(cfg.venc_jpeg, &a);
    if (r != 0) {
        LOGE("VENC_CreateChn jpeg chn=%d failed: 0x%x", cfg.venc_jpeg, (unsigned)r);
        return false;
    }
    VENC_JPEG_PARAM_S jp = { 0 };
    if (rk.venc_get_jpeg(cfg.venc_jpeg, &jp) == 0) {
        jp.u32Qfactor = (RK_U32)cfg.jpeg_qfactor;
        r = rk.venc_set_jpeg(cfg.venc_jpeg, &jp);
        if (r != 0) LOGE("VENC_SetJpegParam failed: 0x%x", (unsigned)r);
    }
    RK_S32 recv = -1;
    r = rk.venc_start(cfg.venc_jpeg, &recv);
    if (r != 0) {
        LOGE("VENC_StartRecvFrame jpeg failed: 0x%x", (unsigned)r);
        rk.venc_destroy(cfg.venc_jpeg);
        return false;
    }
    LOGI("JPEG encoder ready on VENC %d: %ux%u, q%d", cfg.venc_jpeg, w, h, cfg.jpeg_qfactor);
    return true;
}

static sock_ctx_t s_h264 = DEFAULT_SOCK_CTX, s_jpeg = DEFAULT_SOCK_CTX;
static sock_ctx_t s_mjpeg = DEFAULT_SOCK_CTX;

static const uint8_t NAL_AUD[] = { 0x00, 0x00, 0x00, 0x01, 0x09, 0xf0 };

// Encodes one frame and writes it to the given outputs.
static bool encode(int chn, const VIDEO_FRAME_INFO_S *frame, callback_chain_t *chain, bool h264)
{
    RK_S32 r = rk.venc_send(chn, frame, 1000);
    if (r != 0) {
        LOGE("VENC_SendFrame chn=%d failed: 0x%x", chn, (unsigned)r);
        return false;
    }
    VENC_PACK_S pack;
    VENC_STREAM_S st;
    memset(&pack, 0, sizeof(pack));
    memset(&st, 0, sizeof(st));
    st.pstPack = &pack;
    r = rk.venc_get(chn, &st, 1000);
    if (r != 0) {
        LOGE("VENC_GetStream chn=%d failed: 0x%x", chn, (unsigned)r);
        return false;
    }
    if (st.u32PackCount > 1 && !g_warned_multiple_packs) {
        g_warned_multiple_packs = true;
        LOGE("VENC chn=%d returned %u packs, only the first is used",
             chn, st.u32PackCount);
    }
    uint8_t *data = rk.mb_vir(pack.pMbBlk);
    if (data && pack.u32Len > 0) {
        const uint8_t *pos = data + pack.u32Offset;
        if (h264 && s_h264.need_keyframe) {
            if (h264_starts_with_idr(pos, pack.u32Len)) {
                s_h264.need_keyframe = false;
            } else if (!g_warned_missing_idr) {
                g_warned_missing_idr = true;
                LOGE("IDR requested, but the encoder returned another "
                     "frame type; retrying");
            }
        }
        callback_chain_write_cb(pos, pack.u32Len, chain);
        if (h264) callback_chain_write_cb(NAL_AUD, sizeof(NAL_AUD), chain);
    }
    rk.venc_release(chn, &st);
    return true;
}

// ---------------------------------------------------------------------------
// Export thread
// ---------------------------------------------------------------------------

static void *export_thread(void *arg)
{
    (void)arg;
    // unisrv reads a few frames from this channel right after enabling it
    // (its camera check); let that finish first.
    sleep(3);

    if (!load_rockit()) return NULL;

    sock_open(&s_h264, cfg.h264_sock);
    sock_open(&s_jpeg, cfg.jpeg_sock);
    s_jpeg.one_frame = true;
    sock_open(&s_mjpeg, cfg.mjpeg_sock);
    s_mjpeg.allow_drops = true;
    LOGI("serving %s %s %s", cfg.h264_sock, cfg.jpeg_sock, cfg.mjpeg_sock);

    uint64_t stats_time = now_ms();
    unsigned got = 0, errors = 0;
    RK_S32 last_err = 0;

    for (;;) {
        sock_accept_clients(&s_h264, SOCK_SNDBUF_SIZE);
        sock_accept_clients(&s_jpeg, SOCK_SNDBUF_SIZE);
        sock_accept_clients(&s_mjpeg, SOCK_SNDBUF_SIZE);

        bool want_h264 = s_h264.num_clients > 0;
        bool want_jpeg = s_jpeg.num_clients > 0 || s_mjpeg.num_clients > 0;

        if (!want_h264 && !want_jpeg) {
            sock_ctx_t *socks[] = { &s_h264, &s_jpeg, &s_mjpeg, NULL };
            sock_wait_fds(socks, 1000);
            continue;
        }

        VIDEO_FRAME_INFO_S frame;
        memset(&frame, 0, sizeof(frame));
        RK_S32 r = rk.vi_get_frame(cfg.pipe, cfg.chn, &frame, 1000);
        if (r != 0) {
            errors++;
            if (r != last_err) {
                LOGE("VI_GetChnFrame %d:%d failed: 0x%x",
                     cfg.pipe, cfg.chn, (unsigned)r);
            }
            last_err = r;
            usleep(100 * 1000);
            continue;
        }
        last_err = 0;

        uint64_t t = now_ms();
        got++;

        if (want_h264) {
            if (!g_h264_ready) g_h264_ready = venc_h264_open(frame.u32Width, frame.u32Height);
            if (g_h264_ready) {
                // Requested again on every frame until an IDR comes out.
                if (s_h264.need_keyframe) {
                    RK_S32 idr = rk.venc_idr(cfg.venc_h264, 1);
                    if (idr != 0) LOGE("VENC_RequestIDR failed: 0x%x", (unsigned)idr);
                }
                callback_chain_t chain[] = {
                    { sock_write_cb, &s_h264, true },
                    { NULL, NULL, false },
                };
                encode(cfg.venc_h264, &frame, chain, true);
            }
        }
        if (want_jpeg) {
            if (!g_jpeg_ready) g_jpeg_ready = venc_jpeg_open(frame.u32Width, frame.u32Height);
            if (g_jpeg_ready) {
                callback_chain_t chain[] = {
                    { sock_write_cb, &s_jpeg, s_jpeg.num_clients > 0 },
                    { sock_write_cb, &s_mjpeg, s_mjpeg.num_clients > 0 },
                    { NULL, NULL, false },
                };
                encode(cfg.venc_jpeg, &frame, chain, false);
            }
        }
        rk.vi_release_frame(cfg.pipe, cfg.chn, &frame);

        if (t - stats_time >= 30000) {
            LOGI("%.1f fps in, %u frame errors; clients: h264 %d, jpeg %d, mjpeg %d",
                 got / ((t - stats_time) / 1000.0), errors, s_h264.num_clients,
                 s_jpeg.num_clients, s_mjpeg.num_clients);
            got = errors = 0;
            stats_time = t;
        }
    }
    return NULL;
}

// ---------------------------------------------------------------------------
// Hooked calls
// ---------------------------------------------------------------------------

// VENC channels are numbered by the caller, and this library's two must stay
// free for it. Its own calls go to the real function through rk.venc_create, so
// only unisrv and librockit come through here. Without this, unisrv and the
// export would silently share an encoder.
RK_S32 RK_MPI_VENC_CreateChn(RK_S32 chn, const void *attr)
{
    pthread_once(&g_once, init_once);
    if (!load_rockit()) return -1;

    if (g_active) {
        assert(chn != cfg.venc_h264);
        assert(chn != cfg.venc_jpeg);
    }
    return rk.venc_create(chn, attr);
}

RK_S32 RK_MPI_VI_EnableChn(RK_S32 pipe, RK_S32 chn)
{
    pthread_once(&g_once, init_once);
    if (!load_rockit()) return -1;

    if (g_active && pipe == cfg.pipe && chn == cfg.chn) {
        VI_CHN_ATTR_S attr;
        RK_S32 r = rk.vi_get_attr(pipe, chn, &attr);
        if (r != 0) {
            LOGE("VI_GetChnAttr %d:%d failed: 0x%x", pipe, chn, (unsigned)r);
            return r;
        }
        attr.stFrameRate.s32SrcFrameRate = -1;
        attr.stFrameRate.s32DstFrameRate = cfg.fps;
        r = rk.vi_set_attr(pipe, chn, &attr);
        if (r != 0) {
            LOGE("VI_SetChnAttr %d:%d to %d fps failed: 0x%x", pipe, chn, cfg.fps, (unsigned)r);
            return r;
        }
        LOGI("VI %d:%d configured for %d fps", pipe, chn, cfg.fps);
    }

    RK_S32 r = rk.vi_enable_chn ? rk.vi_enable_chn(pipe, chn) : -1;
    if (g_active && r == 0 && pipe == cfg.pipe && chn == cfg.chn) {
        static bool started;
        if (!started) {
            started = true;
            pthread_t t;
            if (pthread_create(&t, NULL, export_thread, NULL) == 0) {
                pthread_detach(t);
                LOGI("VI %d:%d enabled by unisrv, export starting", pipe, chn);
            } else {
                LOGE("cannot start export thread");
            }
        }
    }
    return r;
}
