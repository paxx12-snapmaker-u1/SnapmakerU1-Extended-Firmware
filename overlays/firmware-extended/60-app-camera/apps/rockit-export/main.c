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
//   ROCKIT_EXPORT_RAW_SOCKET    raw NV12 frames
//
// Encoders only run while a socket has clients.
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
#include "log.h"
#include "sock_ctx.h"

// ---------------------------------------------------------------------------
// Minimal Rockit MPI ABI (aarch64), checked against the Rockit SDK headers
// and against how stock 2.1.0 unisrv fills these structures.
// ---------------------------------------------------------------------------

typedef int32_t RK_S32;
typedef uint32_t RK_U32;
typedef uint64_t RK_U64;
typedef void *MB_BLK;

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

#define VENC_CHN_ATTR_SIZE 136
#define VENC_JPEG_PARAM_SIZE 200

#define RK_VIDEO_ID_AVC 8
#define RK_VIDEO_ID_JPEG 15
#define RK_FMT_YUV420SP 0
#define VENC_RC_MODE_H264CBR 1
#define VENC_GOPMODE_NORMALP 1

static void put_u32(uint8_t *base, size_t off, RK_U32 v) { memcpy(base + off, &v, 4); }

// ---------------------------------------------------------------------------
// Config and logging
// ---------------------------------------------------------------------------

static struct {
    char h264_sock[108], jpeg_sock[108], mjpeg_sock[108], raw_sock[108];
    int pipe, chn, venc_h264, venc_jpeg;
    int fps, bitrate_kbps, jpeg_qfactor;
} cfg = {
    .h264_sock = "/tmp/capture-mipi-h264.sock",
    .jpeg_sock = "/tmp/capture-mipi-jpeg.sock",
    .mjpeg_sock = "/tmp/capture-mipi-mjpeg.sock",
    .raw_sock = "/tmp/capture-mipi-raw.sock",
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
    env_str("ROCKIT_EXPORT_RAW_SOCKET", cfg.raw_sock, sizeof(cfg.raw_sock));
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
    LOGI("active in unisrv: VI %d:%d, VENC h264=%d jpeg=%d, %d fps, %d kbps, jpeg q%d",
         cfg.pipe, cfg.chn, cfg.venc_h264, cfg.venc_jpeg, cfg.fps, cfg.bitrate_kbps, cfg.jpeg_qfactor);
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

static bool load_rockit(void)
{
#define LOAD(field, name) if (!(rk.field = dlsym(RTLD_NEXT, name))) { LOGE("missing %s", name); return false; }
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
    return true;
}

// ---------------------------------------------------------------------------
// Sockets
// ---------------------------------------------------------------------------

// Room for about five 1080p JPEG frames, so a client that is a little behind
// never makes a write stall (the default unix socket buffer is ~200 KB).
#define SOCK_SNDBUF_SIZE (1024 * 1024)

static void set_cloexec(int fd)
{
    fcntl(fd, F_SETFD, fcntl(fd, F_GETFD, 0) | FD_CLOEXEC);
}

// sock_ctx.h does not set these, and they matter inside unisrv: a child process
// holding a client fd would keep that client's connection open.
static void open_socket(sock_ctx_t *s, const char *path)
{
    if (sock_open(s, path) == 0)
        set_cloexec(s->listen_fd);
}

static void accept_clients(sock_ctx_t *s)
{
    if (!sock_accept_clients(s))
        return;
    for (int i = 0; i < SOCK_MAX_CLIENTS; i++) {
        int fd = s->clients[i].fd;
        if (fd < 0) continue;
        set_cloexec(fd);
        int sz = SOCK_SNDBUF_SIZE;
        if (setsockopt(fd, SOL_SOCKET, SO_SNDBUFFORCE, &sz, sizeof(sz)) < 0)
            setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sz, sizeof(sz));
    }
}

// ---------------------------------------------------------------------------
// Encoders
// ---------------------------------------------------------------------------

static bool g_h264_ready, g_jpeg_ready;

static bool venc_h264_open(RK_U32 w, RK_U32 h)
{
    uint8_t a[VENC_CHN_ATTR_SIZE] = { 0 };
    put_u32(a, 0, RK_VIDEO_ID_AVC);
    put_u32(a, 4, w);                 // u32MaxPicWidth
    put_u32(a, 8, h);                 // u32MaxPicHeight
    put_u32(a, 12, RK_FMT_YUV420SP);  // enPixelFormat
    put_u32(a, 20, w * h);            // u32BufSize
    put_u32(a, 24, 100);              // u32Profile: high, as capture-v4l2-raw-mpp
    put_u32(a, 28, 1);                // bByFrame
    put_u32(a, 32, w);
    put_u32(a, 36, h);
    put_u32(a, 40, w);                // u32VirWidth
    put_u32(a, 44, h);                // u32VirHeight
    put_u32(a, 48, 4);                // u32StreamBufCnt
    put_u32(a, 52, 41);               // stAttrH264e.u32Level
    put_u32(a, 80, VENC_RC_MODE_H264CBR);
    put_u32(a, 84, (RK_U32)cfg.fps * 2);  // u32Gop: keyframe every 2 s
    put_u32(a, 88, (RK_U32)cfg.fps);      // src fps num
    put_u32(a, 92, 1);
    put_u32(a, 96, (RK_U32)cfg.fps);      // dst fps num
    put_u32(a, 100, 1);
    put_u32(a, 104, (RK_U32)cfg.bitrate_kbps);
    put_u32(a, 108, 3);                   // u32StatTime
    put_u32(a, 120, VENC_GOPMODE_NORMALP);
    RK_S32 r = rk.venc_create(cfg.venc_h264, a);
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
    LOGI("H.264 encoder ready on VENC %d: %ux%u, %d fps, %d kbps", cfg.venc_h264, w, h, cfg.fps, cfg.bitrate_kbps);
    return true;
}

static bool venc_jpeg_open(RK_U32 w, RK_U32 h)
{
    uint8_t a[VENC_CHN_ATTR_SIZE] = { 0 };
    put_u32(a, 0, RK_VIDEO_ID_JPEG);
    put_u32(a, 12, RK_FMT_YUV420SP);
    put_u32(a, 20, w * h * 2);  // same as unisrv's own JPEG channel
    put_u32(a, 32, w);
    put_u32(a, 36, h);
    put_u32(a, 40, w);
    put_u32(a, 44, h);
    put_u32(a, 48, 5);
    RK_S32 r = rk.venc_create(cfg.venc_jpeg, a);
    if (r != 0) {
        LOGE("VENC_CreateChn jpeg chn=%d failed: 0x%x", cfg.venc_jpeg, (unsigned)r);
        return false;
    }
    uint8_t jp[VENC_JPEG_PARAM_SIZE] = { 0 };
    if (rk.venc_get_jpeg(cfg.venc_jpeg, jp) == 0) {
        put_u32(jp, 0, (RK_U32)cfg.jpeg_qfactor);
        r = rk.venc_set_jpeg(cfg.venc_jpeg, jp);
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
static sock_ctx_t s_mjpeg = DEFAULT_SOCK_CTX, s_raw = DEFAULT_SOCK_CTX;

static const uint8_t NAL_AUD[] = { 0x00, 0x00, 0x00, 0x01, 0x09, 0xf0 };

// Checks the first slice of an Annex-B frame. Only the headers before it are
// scanned, as a start code can't occur inside a NAL unit.
static bool h264_starts_with_idr(const uint8_t *data, size_t size)
{
    for (size_t i = 0; i + 3 < size; i++) {
        if (data[i] != 0 || data[i + 1] != 0 || data[i + 2] != 1) continue;
        int type = data[i + 3] & 0x1f;
        if (type == 5) return true;
        if (type == 1) return false;
        i += 2;
    }
    return false;
}

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
    if (st.u32PackCount > 1) {
        static bool warned;
        if (!warned) { warned = true; LOGE("VENC chn=%d returned %u packs, only the first is used", chn, st.u32PackCount); }
    }
    uint8_t *data = rk.mb_vir(pack.pMbBlk);
    if (data && pack.u32Len > 0) {
        const uint8_t *pos = data + pack.u32Offset;
        if (h264 && s_h264.need_keyframe) {
            if (h264_starts_with_idr(pos, pack.u32Len)) {
                s_h264.need_keyframe = false;
            } else {
                static bool warned_idr;
                if (!warned_idr) { warned_idr = true; LOGE("IDR requested, but the encoder returned another frame type; retrying"); }
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

    open_socket(&s_h264, cfg.h264_sock);
    open_socket(&s_jpeg, cfg.jpeg_sock);
    s_jpeg.one_frame = true;
    open_socket(&s_mjpeg, cfg.mjpeg_sock);
    s_mjpeg.allow_drops = true;
    open_socket(&s_raw, cfg.raw_sock);
    LOGI("serving %s %s %s %s", cfg.h264_sock, cfg.jpeg_sock, cfg.mjpeg_sock, cfg.raw_sock);

    const uint64_t frame_ms = 1000 / (uint64_t)cfg.fps;
    uint64_t next_frame = now_ms(), stats_time = now_ms();
    unsigned got = 0, errors = 0;
    RK_S32 last_err = 0;

    for (;;) {
        accept_clients(&s_h264);
        accept_clients(&s_jpeg);
        accept_clients(&s_mjpeg);
        accept_clients(&s_raw);

        bool want_h264 = s_h264.num_clients > 0;
        bool want_jpeg = s_jpeg.num_clients > 0 || s_mjpeg.num_clients > 0;
        bool want_raw = s_raw.num_clients > 0;

        if (!want_h264 && !want_jpeg && !want_raw) {
            sock_ctx_t *socks[] = { &s_h264, &s_jpeg, &s_mjpeg, &s_raw, NULL };
            sock_wait_fds(socks, 1000);
            continue;
        }

        VIDEO_FRAME_INFO_S frame;
        memset(&frame, 0, sizeof(frame));
        RK_S32 r = rk.vi_get_frame(cfg.pipe, cfg.chn, &frame, 1000);
        if (r != 0) {
            errors++;
            if (r != last_err) LOGE("VI_GetChnFrame %d:%d failed: 0x%x", cfg.pipe, cfg.chn, (unsigned)r);
            last_err = r;
            usleep(100 * 1000);
            continue;
        }
        last_err = 0;

        // Keep to the configured frame rate; extra frames are released unused.
        uint64_t t = now_ms();
        if (t + 2 < next_frame) {
            rk.vi_release_frame(cfg.pipe, cfg.chn, &frame);
            continue;
        }
        next_frame = (t > next_frame + frame_ms ? t : next_frame) + frame_ms;
        got++;

        if (want_h264) {
            if (!g_h264_ready) g_h264_ready = venc_h264_open(frame.u32Width, frame.u32Height);
            if (g_h264_ready) {
                // Requested again on every frame until an IDR comes out.
                if (s_h264.need_keyframe) {
                    RK_S32 idr = rk.venc_idr(cfg.venc_h264, 1);
                    if (idr != 0) LOGE("VENC_RequestIDR failed: 0x%x", (unsigned)idr);
                }
                callback_chain_t chain[] = { { sock_write_cb, &s_h264, true }, { NULL, NULL, false } };
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
        if (want_raw) {
            uint8_t *y = rk.mb_vir(frame.pMbBlk);
            if (y) {
                RK_U32 w = frame.u32Width, h = frame.u32Height;
                RK_U32 vw = frame.u32VirWidth ? frame.u32VirWidth : w;
                RK_U32 vh = frame.u32VirHeight ? frame.u32VirHeight : h;
                if (vw == w && vh == h) {
                    sock_write_cb(y, (size_t)w * h * 3 / 2, &s_raw);
                } else {
                    // Repack to tightly packed NV12.
                    static uint8_t *packed;
                    static size_t packed_size;
                    size_t need = (size_t)w * h * 3 / 2;
                    if (packed_size < need) {
                        free(packed);
                        packed = malloc(need);
                        packed_size = packed ? need : 0;
                    }
                    if (packed) {
                        for (RK_U32 row = 0; row < h; row++)
                            memcpy(packed + (size_t)row * w, y + (size_t)row * vw, w);
                        const uint8_t *uv = y + (size_t)vw * vh;
                        for (RK_U32 row = 0; row < h / 2; row++)
                            memcpy(packed + (size_t)w * h + (size_t)row * w, uv + (size_t)row * vw, w);
                        sock_write_cb(packed, need, &s_raw);
                    }
                }
            }
        }

        rk.vi_release_frame(cfg.pipe, cfg.chn, &frame);

        if (t - stats_time >= 30000) {
            LOGI("%.1f fps in, %u frame errors; clients: h264 %d, jpeg %d, mjpeg %d, raw %d",
                 got / ((t - stats_time) / 1000.0), errors, s_h264.num_clients,
                 s_jpeg.num_clients, s_mjpeg.num_clients, s_raw.num_clients);
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
    static RK_S32 (*real)(RK_S32, const void *);
    if (!real) real = dlsym(RTLD_NEXT, "RK_MPI_VENC_CreateChn");

    pthread_once(&g_once, init_once);
    if (g_active) {
        assert(chn != cfg.venc_h264);
        assert(chn != cfg.venc_jpeg);
    }
    return real ? real(chn, attr) : -1;
}

RK_S32 RK_MPI_VI_EnableChn(RK_S32 pipe, RK_S32 chn)
{
    if (!rk.vi_enable_chn) rk.vi_enable_chn = dlsym(RTLD_NEXT, "RK_MPI_VI_EnableChn");
    RK_S32 r = rk.vi_enable_chn ? rk.vi_enable_chn(pipe, chn) : -1;

    pthread_once(&g_once, init_once);
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
