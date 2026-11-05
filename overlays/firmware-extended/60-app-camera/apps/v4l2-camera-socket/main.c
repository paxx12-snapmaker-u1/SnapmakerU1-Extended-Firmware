// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <poll.h>
#include <dlfcn.h>
#include <linux/videodev2.h>
#include <linux/dma-buf.h>

#define MAX_BUFFERS 4
#define MAX_BUFFER_SIZE (4 * 1024 * 1024)

#define FOURCC_ARGS(f) (f) & 0xff, ((f) >> 8) & 0xff, ((f) >> 16) & 0xff, ((f) >> 24) & 0xff
#define FOURCC_FMT "%c%c%c%c"

#define LOG_PREFIX "[v4l2-mpp-injector] "
#define LOG_DEBUG(fmt, ...) do { if (cfg_debug) fprintf(stderr, LOG_PREFIX "DEBUG[%d]: " fmt "\n", getpid(), ##__VA_ARGS__); } while(0)
#define LOG_INFO(fmt, ...) fprintf(stderr, LOG_PREFIX "INFO[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)
#define LOG_ERROR(fmt, ...) fprintf(stderr, LOG_PREFIX "ERROR[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)

static pthread_mutex_t g_mutex = PTHREAD_MUTEX_INITIALIZER;
static int g_initialized = 0;

static char cfg_socket_path[256] = "";
static char cfg_device[256] = "/dev/video0";
static int cfg_width = 1920;
static int cfg_height = 1080;
static uint32_t cfg_format = V4L2_PIX_FMT_MJPEG;
static int cfg_debug = 0;
static int cfg_socket_timeout = 1000;

static int g_pipe_rd = -1;
static int g_pipe_wr = -1;
static int g_streaming = 0;
static int g_buffer_count = 0;

typedef struct {
    void *ptr;
    size_t size;
    size_t used;
    int dmabuf_fd;
    bool queued;
} buffer_t;

static buffer_t g_buffers[MAX_BUFFERS];

static bool is_compressed(void)
{
    return cfg_format == V4L2_PIX_FMT_MJPEG || cfg_format == V4L2_PIX_FMT_JPEG;
}

static size_t bytes_per_line(void)
{
    switch (cfg_format) {
    case V4L2_PIX_FMT_NV12:
        return cfg_width;
    case V4L2_PIX_FMT_YUYV:
        return cfg_width * 2;
    default:
        return 0;
    }
}

// Raw formats have a fixed frame size, compressed ones get a worst-case buffer
static size_t image_size(void)
{
    switch (cfg_format) {
    case V4L2_PIX_FMT_NV12:
        return (size_t)cfg_width * cfg_height * 3 / 2;
    default:
        return (size_t)cfg_width * cfg_height * 2;
    }
}

static void free_buffers(void)
{
    int i;

    for (i = 0; i < MAX_BUFFERS; i++) {
        if (g_buffers[i].ptr != MAP_FAILED) {
            munmap(g_buffers[i].ptr, g_buffers[i].size);
            g_buffers[i].ptr = MAP_FAILED;
        }
        g_buffers[i].size = 0;
        g_buffers[i].used = 0;
        g_buffers[i].dmabuf_fd = -1;
    }
    g_buffer_count = 0;
}

static bool load_config(void)
{
    const char *env;

    if (g_initialized)
        return true;

    // check if current process is unisrv, read self process name
    char self_path[256];
    ssize_t len = readlink("/proc/self/exe", self_path, sizeof(self_path) - 1);
    if (len >= 0) {
        self_path[len] = '\0';
        const char *basename = strrchr(self_path, '/');
        basename = basename ? basename + 1 : self_path;
        if (strcmp(basename, "unisrv") != 0) {
            LOG_INFO("Current process is not unisrv (%s)", basename);
            return false;
        }
    }

    g_initialized = 1;

    env = getenv("V4L2_IMPOSTER_DEBUG");
    if (env && atoi(env))
        cfg_debug = 1;

    env = getenv("V4L2_IMPOSTER_SOCKET_PATH");
    if (env && strlen(env) > 0) {
        strncpy(cfg_socket_path, env, sizeof(cfg_socket_path) - 1);
        cfg_socket_path[sizeof(cfg_socket_path) - 1] = '\0';
    }

    env = getenv("V4L2_IMPOSTER_DEVICE");
    if (env && strlen(env) > 0) {
        strncpy(cfg_device, env, sizeof(cfg_device) - 1);
        cfg_device[sizeof(cfg_device) - 1] = '\0';
    }

    env = getenv("V4L2_IMPOSTER_WIDTH");
    if (env)
        cfg_width = atoi(env);

    env = getenv("V4L2_IMPOSTER_HEIGHT");
    if (env)
        cfg_height = atoi(env);

    env = getenv("V4L2_IMPOSTER_FORMAT");
    if (env) {
        if (strcasecmp(env, "MJPEG") == 0)
            cfg_format = V4L2_PIX_FMT_MJPEG;
        else if (strcasecmp(env, "JPEG") == 0)
            cfg_format = V4L2_PIX_FMT_JPEG;
        else if (strcasecmp(env, "YUYV") == 0)
            cfg_format = V4L2_PIX_FMT_YUYV;
        else if (strcasecmp(env, "NV12") == 0)
            cfg_format = V4L2_PIX_FMT_NV12;
        else {
            LOG_ERROR("Unsupported format: %s", env);
            return false;
        }
    }

    env = getenv("V4L2_IMPOSTER_SOCKET_TIMEOUT");
    if (env)
        cfg_socket_timeout = atoi(env);

    LOG_INFO("Config: socket=%s device=%s width=%d height=%d format=" FOURCC_FMT " timeout=%d",
              cfg_socket_path, cfg_device, cfg_width, cfg_height, FOURCC_ARGS(cfg_format), cfg_socket_timeout);
    return true;
}

// unisrv captures through librockit, which dlopen()s libv4l2 and resolves
// v4l2_* with dlsym() on that handle, bypassing LD_PRELOAD. Hook dlsym() to
// hand out the functions below, and keep the real ones for every fd that is
// not the impersonated device. librockit never calls v4l2_dup, v4l2_read or
// v4l2_mmap, and uses DMABUF buffers, so v4l2_munmap is not needed either.
static int (*real_v4l2_open)(const char *, int, ...);
static int (*real_v4l2_close)(int);
static int (*real_v4l2_ioctl)(int, unsigned long, ...);

static void *(*g_real_dlsym)(void *, const char *);

static void load_real_dlsym(void)
{
    if (!g_real_dlsym)
        g_real_dlsym = dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.34");
    if (!g_real_dlsym)
        g_real_dlsym = dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.17");
}

int v4l2_open(const char *file, int oflag, ...);
int v4l2_close(int fd);
int v4l2_ioctl(int fd, unsigned long request, ...);

#define HOOK(name) \
    if (strcmp(sym_name, #name) == 0) { \
        LOG_DEBUG("dlsym(%s): redirected to v4l2-imposter", sym_name); \
        real_##name = (void *)sym; \
        return (void *)name; \
    }

void *dlsym(void *handle, const char *sym_name)
{
    // dlsym(RTLD_NEXT) resolves relative to its caller (e.g. the firmware
    // imposter preloaded next to this library), so it must reach the real
    // dlsym as a tail call, with the caller's return address.
    load_real_dlsym();
    if (!g_real_dlsym)
        return NULL;
    if (handle == RTLD_NEXT || strncmp(sym_name, "v4l2_", 5) != 0)
        return g_real_dlsym(handle, sym_name);

    void *sym = g_real_dlsym(handle, sym_name);

    if (!sym || !load_config())
        return sym;

    HOOK(v4l2_open);
    HOOK(v4l2_close);
    HOOK(v4l2_ioctl);
    return sym;
}

static int handle_open(const char *file, int oflag)
{
    int pipefd[2];

    (void)oflag;

    if (g_pipe_rd >= 0) {
        LOG_ERROR("v4l2_open(%s): already open (fd %d)", file, g_pipe_rd);
        errno = EBUSY;
        return -1;
    }

    if (pipe(pipefd) < 0) {
        LOG_ERROR("v4l2_open(%s): pipe failed: %s", file, strerror(errno));
        return -1;
    }

    fcntl(pipefd[0], F_SETFL, O_NONBLOCK);
    fcntl(pipefd[1], F_SETFL, O_NONBLOCK);

    g_pipe_rd = pipefd[0];
    g_pipe_wr = pipefd[1];
    LOG_DEBUG("v4l2_open(%s) -> fd %d", file, g_pipe_rd);

    return g_pipe_rd;
}

int v4l2_open(const char *file, int oflag, ...)
{
    if (!load_config() || (cfg_device[0] != '\0' && strcmp(file, cfg_device) != 0)) {
        va_list ap;
        va_start(ap, oflag);
        int mode = va_arg(ap, int);
        va_end(ap);
        return real_v4l2_open(file, oflag, mode);
    }

    pthread_mutex_lock(&g_mutex);
    int fd = handle_open(file, oflag);
    pthread_mutex_unlock(&g_mutex);
    return fd;
}

int v4l2_close(int fd)
{
    if (fd < 0 || fd != g_pipe_rd)
        return real_v4l2_close(fd);

    pthread_mutex_lock(&g_mutex);

    if (fd == g_pipe_rd) {
        LOG_DEBUG("v4l2_close(%d)", fd);
        free_buffers();
        close(g_pipe_rd);
        close(g_pipe_wr);
        g_pipe_rd = -1;
        g_pipe_wr = -1;
        g_streaming = 0;
    }

    pthread_mutex_unlock(&g_mutex);

    return 0;
}

static int connect_to_socket(void)
{
    struct sockaddr_un addr;
    int fd;

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        LOG_ERROR("Failed to create socket: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, cfg_socket_path, sizeof(addr.sun_path) - 1);
    addr.sun_path[sizeof(addr.sun_path) - 1] = '\0';

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        LOG_ERROR("Failed to connect to %s: %s", cfg_socket_path, strerror(errno));
        close(fd);
        return -1;
    }

    LOG_DEBUG("Connected to socket %s (fd=%d)", cfg_socket_path, fd);
    return fd;
}

static ssize_t read_once(int fd, void *buf, size_t count, int timeout_ms)
{
    struct pollfd pfd;
    ssize_t n;
    int ret;

    for (;;) {
        pfd.fd = fd;
        pfd.events = POLLIN;
        pfd.revents = 0;

        ret = poll(&pfd, 1, timeout_ms);
        if (ret < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (ret == 0) {
            errno = ETIMEDOUT;
            return -1;
        }
        if (pfd.revents & POLLERR) {
            errno = ECONNRESET;
            return -1;
        }
        if (pfd.revents & POLLHUP)
            return 0;

        n = read(fd, buf, count);
        if (n < 0 && errno == EINTR)
            continue;
        return n;
    }
}

static ssize_t read_fully(int fd, void *buf, size_t count, int timeout_ms)
{
    size_t total = 0;
    ssize_t n;

    while (total < count) {
        n = read_once(fd, (char *)buf + total, count - total, timeout_ms);
        if (n < 0)
            return -1;
        if (n == 0)
            break;
        total += n;
    }
    return total;
}

static int fetch_frame_once(buffer_t *buf)
{
    ssize_t n;
    int fd;
    struct timespec ts, ts2;
    clock_gettime(CLOCK_MONOTONIC, &ts);

    fd = connect_to_socket();
    if (fd < 0)
        return -1;

    // The raw frame socket streams frames back to back without closing,
    // so read exactly one frame (buffer is sized to it) and disconnect.
    struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE };
    ioctl(buf->dmabuf_fd, DMA_BUF_IOCTL_SYNC, &sync);
    n = read_fully(fd, buf->ptr, buf->size, cfg_socket_timeout);
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE;
    ioctl(buf->dmabuf_fd, DMA_BUF_IOCTL_SYNC, &sync);
    close(fd);

    if (n < 0) {
        LOG_ERROR("Failed to read frame data: %s", strerror(errno));
        return -1;
    }
    if (n == 0) {
        LOG_ERROR("No data received");
        return -1;
    }
    // The server drops slow clients mid-frame, which shows up as a short read
    if (!is_compressed() && (size_t)n != buf->size) {
        LOG_ERROR("Truncated frame: got %zd of %zu bytes", n, buf->size);
        return -1;
    }

    clock_gettime(CLOCK_MONOTONIC, &ts2);

    long diff_ms = (ts2.tv_sec - ts.tv_sec) * 1000 + (ts2.tv_nsec - ts.tv_nsec) / 1000000;

    buf->used = n;
    LOG_DEBUG("Fetched frame: %zd bytes in %ld ms", n, diff_ms);
    return 0;
}

static int fetch_frame(buffer_t *buf)
{
    for (int attempt = 0; attempt < 3; attempt++) {
        if (fetch_frame_once(buf) == 0)
            return 0;
    }
    return -1;
}

static int handle_querycap(struct v4l2_capability *cap)
{
    memset(cap, 0, sizeof(*cap));
    strncpy((char *)cap->driver, "v4l2-imposter", sizeof(cap->driver) - 1);
    snprintf((char *)cap->card, sizeof(cap->card), "Imposter %s", cfg_device);
    strncpy((char *)cap->bus_info, cfg_socket_path, sizeof(cap->bus_info) - 1);
    cap->version = 0x00050400;
    cap->capabilities = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING | V4L2_CAP_DEVICE_CAPS;
    cap->device_caps = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING;
    LOG_DEBUG("QUERYCAP: driver=%s card=%s bus_info=%s",
              cap->driver, cap->card, cap->bus_info);
    return 0;
}

static int handle_s_fmt(struct v4l2_format *fmt)
{
    if (fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("S_FMT: invalid type %u", fmt->type);
        return -EINVAL;
    }

    if (fmt->fmt.pix.width != (unsigned)cfg_width ||
        fmt->fmt.pix.height != (unsigned)cfg_height) {
        LOG_ERROR("S_FMT: requested %ux%u but configured %dx%d",
                  fmt->fmt.pix.width, fmt->fmt.pix.height, cfg_width, cfg_height);
        return -EINVAL;
    }

    if (fmt->fmt.pix.pixelformat != cfg_format) {
        LOG_ERROR("S_FMT: requested format " FOURCC_FMT " but configured " FOURCC_FMT,
                  FOURCC_ARGS(fmt->fmt.pix.pixelformat), FOURCC_ARGS(cfg_format));
        return -EINVAL;
    }

    fmt->fmt.pix.field = V4L2_FIELD_NONE;
    fmt->fmt.pix.bytesperline = bytes_per_line();
    fmt->fmt.pix.sizeimage = image_size();
    if (fmt->fmt.pix.sizeimage > MAX_BUFFER_SIZE) {
        LOG_ERROR("S_FMT: sizeimage overflow");
        return -EINVAL;
    }
    fmt->fmt.pix.colorspace = V4L2_COLORSPACE_JPEG;
    LOG_DEBUG("S_FMT: %dx%d fmt=" FOURCC_FMT, cfg_width, cfg_height, FOURCC_ARGS(cfg_format));
    return 0;
}

static int handle_reqbufs(struct v4l2_requestbuffers *req)
{
    int i;
    size_t size;

    if (req->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("REQBUFS: invalid type %u", req->type);
        return -EINVAL;
    }
    // librockit allocates the buffers and queues them as dma-buf fds
    if (req->memory != V4L2_MEMORY_DMABUF) {
        LOG_DEBUG("REQBUFS: invalid memory %u", req->memory);
        return -EINVAL;
    }

    free_buffers();

    if (req->count == 0)
        return 0;

    if (req->count > MAX_BUFFERS)
        req->count = MAX_BUFFERS;

    size = image_size();
    if (size > MAX_BUFFER_SIZE) {
        LOG_ERROR("REQBUFS: buffer size overflow");
        return -EINVAL;
    }

    for (i = 0; i < (int)req->count; i++) {
        g_buffers[i].ptr = MAP_FAILED;
        g_buffers[i].size = size;
        g_buffers[i].used = 0;
        g_buffers[i].dmabuf_fd = -1;
        g_buffers[i].queued = false;
    }

    g_buffer_count = i;
    req->count = g_buffer_count;
    LOG_DEBUG("REQBUFS: allocated %d buffers (%zu bytes each)", g_buffer_count, size);
    return 0;
}

static int handle_qbuf(struct v4l2_buffer *buf)
{
    if (buf->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("QBUF: invalid type %u", buf->type);
        return -EINVAL;
    }
    if (buf->index >= (unsigned)g_buffer_count) {
        LOG_DEBUG("QBUF: invalid index %u (count=%d)", buf->index, g_buffer_count);
        return -EINVAL;
    }
    if (g_buffers[buf->index].dmabuf_fd != buf->m.fd) {
        buffer_t *b = &g_buffers[buf->index];
        if (buf->length && buf->length < b->size) {
            LOG_ERROR("QBUF: dma-buf %d is %u bytes, need %zu", buf->m.fd, buf->length, b->size);
            return -EINVAL;
        }
        if (b->ptr != MAP_FAILED)
            munmap(b->ptr, b->size);
        b->ptr = mmap(NULL, b->size, PROT_READ | PROT_WRITE, MAP_SHARED, buf->m.fd, 0);
        b->dmabuf_fd = b->ptr != MAP_FAILED ? buf->m.fd : -1;
        if (b->ptr == MAP_FAILED)
            LOG_ERROR("QBUF: mmap of dma-buf %d failed: %s", buf->m.fd, strerror(errno));
    }
    if (g_buffers[buf->index].ptr == MAP_FAILED) {
        LOG_DEBUG("QBUF: buffer %u not allocated", buf->index);
        return -EINVAL;
    }
    if (g_buffers[buf->index].queued) {
        LOG_DEBUG("QBUF: buffer %u already queued", buf->index);
        return -EINVAL;
    }

    g_buffers[buf->index].queued = true;

    if (g_streaming) {
        uint8_t idx = buf->index;
        if (write(g_pipe_wr, &idx, 1) != 1) {
            LOG_DEBUG("QBUF: write to pipe failed");
            g_buffers[buf->index].queued = false;
            return -EIO;
        }
    }
    LOG_DEBUG("QBUF: index=%d", buf->index);
    return 0;
}

static int handle_dqbuf(struct v4l2_buffer *buf)
{
    uint8_t idx;
    ssize_t n;

    if (buf->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("DQBUF: invalid type %u", buf->type);
        return -EINVAL;
    }
    if (!g_streaming) {
        LOG_DEBUG("QBUF: not streaming");
        return -EINVAL;
    }

    n = read(g_pipe_rd, &idx, 1);
    if (n != 1) {
        if (n < 0 && errno == EAGAIN) {
            LOG_DEBUG("DQBUF: no queued buffer");
            return -EAGAIN;
        }
        LOG_DEBUG("DQBUF: read from pipe failed");
        return -EIO;
    }

    if (idx >= g_buffer_count) {
        LOG_DEBUG("DQBUF: invalid index %u from pipe", idx);
        return -EIO;
    }

    if (!g_buffers[idx].queued) {
        LOG_DEBUG("DQBUF: buffer %u not queued", idx);
        return -EINVAL;
    }

    if (fetch_frame(&g_buffers[idx]) < 0) {
        if (write(g_pipe_wr, &idx, 1) != 1) {
            LOG_ERROR("DQBUF: failed to re-queue buffer %u", idx);
            g_buffers[idx].queued = false;
        }
        return -EIO;
    }

    g_buffers[idx].queued = false;

    buf->index = idx;
    buf->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf->memory = V4L2_MEMORY_DMABUF;
    buf->bytesused = g_buffers[idx].used;
    buf->length = g_buffers[idx].size;
    buf->m.fd = g_buffers[idx].dmabuf_fd;
    buf->flags = V4L2_BUF_FLAG_DONE;

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    buf->timestamp.tv_sec = ts.tv_sec;
    buf->timestamp.tv_usec = ts.tv_nsec / 1000;

    LOG_DEBUG("DQBUF: index=%u bytesused=%u", idx, buf->bytesused);
    return 0;
}

static int handle_streamon(enum v4l2_buf_type *type)
{
    if (*type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("STREAMON: invalid type %u", *type);
        return -EINVAL;
    }
    if (g_streaming) {
        LOG_DEBUG("STREAMON: already streaming");
        return -EINVAL;
    }

    for (int i = 0; i < g_buffer_count; i++) {
        if (g_buffers[i].ptr == MAP_FAILED) {
            LOG_DEBUG("STREAMON: buffer %d not allocated", i);
            continue;
        }
        if (!g_buffers[i].queued) {
            LOG_DEBUG("STREAMON: buffer %d not queued", i);
            continue;
        }
        uint8_t idx = i;
        if (write(g_pipe_wr, &idx, 1) != 1) {
            LOG_DEBUG("STREAMON: write to pipe failed");
            return -EIO;
        }
    }

    g_streaming = 1;
    LOG_DEBUG("STREAMON");
    return 0;
}

static int handle_streamoff(enum v4l2_buf_type *type)
{
    uint8_t idx;

    if (*type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("STREAMOFF: invalid type %u", *type);
        return -EINVAL;
    }
    if (!g_streaming) {
        LOG_DEBUG("STREAMOFF: not streaming");
        return -EINVAL;
    }

    g_streaming = 0;
    while (read(g_pipe_rd, &idx, 1) == 1) {
        if (idx < g_buffer_count)
            g_buffers[idx].queued = false;
    }

    LOG_DEBUG("STREAMOFF");
    return 0;
}

int v4l2_ioctl(int fd, unsigned long request, ...)
{
    va_list ap;
    void *arg;
    int ret = -ENOTTY;

    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);

    if (fd < 0 || fd != g_pipe_rd)
        return real_v4l2_ioctl(fd, request, arg);

    request &= 0xffffffff;

    pthread_mutex_lock(&g_mutex);
    if (fd != g_pipe_rd || g_pipe_rd < 0) {
        pthread_mutex_unlock(&g_mutex);
        errno = EBADF;
        return -1;
    }

    switch (request) {
    case VIDIOC_QUERYCAP:
        ret = handle_querycap(arg);
        break;
    case VIDIOC_S_FMT:
        ret = handle_s_fmt(arg);
        break;
    case VIDIOC_REQBUFS:
        ret = handle_reqbufs(arg);
        break;
    case VIDIOC_QBUF:
        ret = handle_qbuf(arg);
        break;
    case VIDIOC_DQBUF:
        ret = handle_dqbuf(arg);
        break;
    case VIDIOC_STREAMON:
        ret = handle_streamon(arg);
        break;
    case VIDIOC_STREAMOFF:
        ret = handle_streamoff(arg);
        break;
    default:
        LOG_DEBUG("Unhandled ioctl 0x%lx", request);
        ret = -ENOTTY;
        break;
    }

    pthread_mutex_unlock(&g_mutex);

    if (ret < 0) {
        errno = -ret;
        return -1;
    }
    return ret;
}
