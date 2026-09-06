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
#include <linux/videodev2.h>

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
static char cfg_format_str[32] = "MJPEG";
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
    size_t offset;
    bool queued;
} buffer_t;

static buffer_t g_buffers[MAX_BUFFERS];

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
        strncpy(cfg_format_str, env, sizeof(cfg_format_str) - 1);
        cfg_format_str[sizeof(cfg_format_str) - 1] = '\0';
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


static int handle_open(const char *file, int oflag)
{
    int pipefd[2];

    (void)oflag;

    if (!load_config()) {
        errno = EINVAL;
        return -1;
    }

    if (cfg_device[0] != '\0' && strcmp(file, cfg_device) != 0) {
        LOG_DEBUG("v4l2_open(%s): not target device %s", file, cfg_device);
        errno = ENOENT;
        return -1;
    }

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
    pthread_mutex_lock(&g_mutex);
    int fd = handle_open(file, oflag);
    pthread_mutex_unlock(&g_mutex);
    return fd;
}

int v4l2_close(int fd)
{
    if (!g_initialized) {
        errno = EINVAL;
        return -1;
    }

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

static int fetch_frame(buffer_t *buf)
{
    ssize_t n;
    int fd;
    struct timespec ts, ts2;
    clock_gettime(CLOCK_MONOTONIC, &ts);

    fd = connect_to_socket();
    if (fd < 0)
        return -1;

    n = read_fully(fd, buf->ptr, buf->size, cfg_socket_timeout);
    close(fd);

    if (n < 0) {
        LOG_ERROR("Failed to read frame data: %s", strerror(errno));
        return -1;
    }
    if (n == 0) {
        LOG_ERROR("No data received");
        return -1;
    }
    if ((size_t)n > buf->size) {
        LOG_ERROR("Frame size %zd exceeds buffer size %zu", n, buf->size);
        return -1;
    }

    clock_gettime(CLOCK_MONOTONIC, &ts2);

    long diff_ms = (ts2.tv_sec - ts.tv_sec) * 1000 + (ts2.tv_nsec - ts.tv_nsec) / 1000000;

    buf->used = n;
    LOG_DEBUG("Fetched frame: %zd bytes in %ld ms", n, diff_ms);
    return 0;
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

static int handle_enum_fmt(struct v4l2_fmtdesc *fmt)
{
    if (fmt->index > 0) {
        LOG_DEBUG("ENUM_FMT: invalid index %u", fmt->index);
        return -EINVAL;
    }
    if (fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("ENUM_FMT: invalid type %u", fmt->type);
        return -EINVAL;
    }

    memset(fmt, 0, sizeof(*fmt));
    fmt->index = 0;
    fmt->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt->pixelformat = cfg_format;
    if (cfg_format == V4L2_PIX_FMT_MJPEG || cfg_format == V4L2_PIX_FMT_JPEG)
        fmt->flags = V4L2_FMT_FLAG_COMPRESSED;
    strncpy((char *)fmt->description, cfg_format_str, sizeof(fmt->description) - 1);
    LOG_DEBUG("ENUM_FMT index=%u format=%s", fmt->index, cfg_format_str);
    return 0;
}

static int handle_g_fmt(struct v4l2_format *fmt)
{
    if (fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("G_FMT: invalid type %u", fmt->type);
        return -EINVAL;
    }

    fmt->fmt.pix.width = cfg_width;
    fmt->fmt.pix.height = cfg_height;
    fmt->fmt.pix.pixelformat = cfg_format;
    fmt->fmt.pix.field = V4L2_FIELD_NONE;
    fmt->fmt.pix.bytesperline = 0;
    fmt->fmt.pix.sizeimage = cfg_width * cfg_height * 2;
    if (fmt->fmt.pix.sizeimage > MAX_BUFFER_SIZE) {
        LOG_ERROR("G_FMT: sizeimage overflow");
        return -EINVAL;
    }
    fmt->fmt.pix.colorspace = V4L2_COLORSPACE_JPEG;
    LOG_DEBUG("G_FMT: %dx%d fmt=" FOURCC_FMT, cfg_width, cfg_height, FOURCC_ARGS(cfg_format));
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
    fmt->fmt.pix.bytesperline = 0;
    fmt->fmt.pix.sizeimage = cfg_width * cfg_height * 2;
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
    if (req->memory != V4L2_MEMORY_MMAP) {
        LOG_DEBUG("REQBUFS: invalid memory %u", req->memory);
        return -EINVAL;
    }

    free_buffers();

    if (req->count == 0)
        return 0;

    if (req->count > MAX_BUFFERS)
        req->count = MAX_BUFFERS;

    size = cfg_width * cfg_height * 2;
    if (size > MAX_BUFFER_SIZE) {
        LOG_ERROR("REQBUFS: buffer size overflow");
        return -EINVAL;
    }

    size_t offset = 0;

    for (i = 0; i < (int)req->count; i++) {
        g_buffers[i].ptr = MAP_FAILED;
        g_buffers[i].size = size;
        g_buffers[i].used = 0;
        g_buffers[i].offset = offset;
        g_buffers[i].queued = false;
        LOG_DEBUG("The buffer %d: size=%zu offset=0x%zx", i, size, offset);
        offset += size;
    }

    g_buffer_count = i;
    req->count = g_buffer_count;
    LOG_DEBUG("REQBUFS: allocated %d buffers (%zu bytes each)", g_buffer_count, size);
    return 0;
}

static int handle_querybuf(struct v4l2_buffer *buf)
{
    if (buf->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("QUERYBUF: invalid type %u", buf->type);
        return -EINVAL;
    }
    if (buf->index >= (unsigned)g_buffer_count) {
        LOG_DEBUG("QUERYBUF: invalid index %u (count=%d)", buf->index, g_buffer_count);
        return -EINVAL;
    }

    buf->memory = V4L2_MEMORY_MMAP;
    buf->length = g_buffers[buf->index].size;
    buf->m.offset = g_buffers[buf->index].offset;
    buf->flags = 0;
    LOG_DEBUG("QUERYBUF: index=%d offset=0x%x length=%u",
              buf->index, buf->m.offset, buf->length);
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
    buf->memory = V4L2_MEMORY_MMAP;
    buf->bytesused = g_buffers[idx].used;
    buf->length = g_buffers[idx].size;
    buf->m.offset = g_buffers[idx].offset;
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

static int handle_g_parm(struct v4l2_streamparm *parm)
{
    if (parm->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("G_PARM: invalid type %u", parm->type);
        return -EINVAL;
    }

    memset(&parm->parm, 0, sizeof(parm->parm));
    parm->parm.capture.capability = V4L2_CAP_TIMEPERFRAME;
    parm->parm.capture.timeperframe.numerator = 1;
    parm->parm.capture.timeperframe.denominator = 30;
    LOG_DEBUG("G_PARM");
    return 0;
}

static int handle_s_parm(struct v4l2_streamparm *parm)
{
    if (parm->type != V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        LOG_DEBUG("S_PARM: invalid type %u", parm->type);
        return -EINVAL;
    }

    parm->parm.capture.capability = V4L2_CAP_TIMEPERFRAME;
    if (parm->parm.capture.timeperframe.denominator == 0)
        parm->parm.capture.timeperframe.denominator = 30;
    if (parm->parm.capture.timeperframe.numerator == 0)
        parm->parm.capture.timeperframe.numerator = 1;
    LOG_DEBUG("S_PARM");
    return 0;
}

static int handle_enum_framesizes(struct v4l2_frmsizeenum *fsize)
{
    if (fsize->index > 0) {
        LOG_DEBUG("ENUM_FRAMESIZES: invalid index %u", fsize->index);
        return -EINVAL;
    }
    if (fsize->pixel_format != cfg_format) {
        LOG_DEBUG("ENUM_FRAMESIZES: invalid format " FOURCC_FMT, FOURCC_ARGS(fsize->pixel_format));
        return -EINVAL;
    }

    fsize->type = V4L2_FRMSIZE_TYPE_DISCRETE;
    fsize->discrete.width = cfg_width;
    fsize->discrete.height = cfg_height;
    LOG_DEBUG("ENUM_FRAMESIZES");
    return 0;
}

static int handle_enum_frameintervals(struct v4l2_frmivalenum *fival)
{
    if (fival->index > 0) {
        LOG_DEBUG("ENUM_FRAMEINTERVALS: invalid index %u", fival->index);
        return -EINVAL;
    }

    fival->type = V4L2_FRMIVAL_TYPE_DISCRETE;
    fival->discrete.numerator = 1;
    fival->discrete.denominator = 30;
    LOG_DEBUG("ENUM_FRAMEINTERVALS");
    return 0;
}

int v4l2_ioctl(int fd, unsigned long request, ...)
{
    va_list ap;
    void *arg;
    int ret = -ENOTTY;

    if (!g_initialized) {
        errno = EINVAL;
        return -1;
    }

    va_start(ap, request);
    arg = va_arg(ap, void *);
    va_end(ap);

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
    case VIDIOC_ENUM_FMT:
        ret = handle_enum_fmt(arg);
        break;
    case VIDIOC_G_FMT:
        ret = handle_g_fmt(arg);
        break;
    case VIDIOC_S_FMT:
    case VIDIOC_TRY_FMT:
        ret = handle_s_fmt(arg);
        break;
    case VIDIOC_REQBUFS:
        ret = handle_reqbufs(arg);
        break;
    case VIDIOC_QUERYBUF:
        ret = handle_querybuf(arg);
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
    case VIDIOC_G_PARM:
        ret = handle_g_parm(arg);
        break;
    case VIDIOC_S_PARM:
        ret = handle_s_parm(arg);
        break;
    case VIDIOC_ENUM_FRAMESIZES:
        ret = handle_enum_framesizes(arg);
        break;
    case VIDIOC_ENUM_FRAMEINTERVALS:
        ret = handle_enum_frameintervals(arg);
        break;
    case VIDIOC_G_INPUT:
        *(int *)arg = 0;
        ret = 0;
        break;
    case VIDIOC_S_INPUT:
        ret = 0;
        break;
    case VIDIOC_ENUMINPUT:
        {
            struct v4l2_input *inp = arg;
            if (inp->index > 0) {
                ret = -EINVAL;
            } else {
                memset(inp, 0, sizeof(*inp));
                inp->index = 0;
                strncpy((char *)inp->name, "Camera", sizeof(inp->name) - 1);
                inp->type = V4L2_INPUT_TYPE_CAMERA;
                ret = 0;
            }
        }
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

static void *handle_mmap(void *start, size_t length, int prot, int flags, int fd, int64_t offset)
{
    (void)start;
    (void)length;
    (void)prot;
    (void)flags;

    LOG_DEBUG("v4l2_mmap(fd=%d, start=%p, length=%zu, offset=0x%lx)", fd, start, length, (unsigned long)offset);

    if (!g_initialized) {
        errno = EINVAL;
        return NULL;
    }

    if (start != NULL) {
        errno = EINVAL;
        return MAP_FAILED;
    }

    if (fd != g_pipe_rd) {
        errno = EBADF;
        return MAP_FAILED;
    }

    for (int i = 0; i < g_buffer_count; i++) {
        if (g_buffers[i].offset != (size_t)offset)
            continue;
        if (g_buffers[i].size != length) {
            LOG_ERROR("v4l2_mmap: buffer %d size mismatch (expected %zu, got %zu)",
                      i, g_buffers[i].size, length);
            errno = EINVAL;
            return MAP_FAILED;
        }
        if (g_buffers[i].ptr == MAP_FAILED) {
            LOG_DEBUG("v4l2_mmap: allocating buffer %d of size %zu", i, g_buffers[i].size);
            g_buffers[i].ptr = mmap(NULL, g_buffers[i].size, PROT_READ | PROT_WRITE,
                                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        }
        if (g_buffers[i].ptr == MAP_FAILED) {
            LOG_ERROR("v4l2_mmap: failed to allocate buffer %d", i);
            break;
        }
        LOG_DEBUG("v4l2_mmap: returning buffer %d at %p", i, g_buffers[i].ptr);
        return g_buffers[i].ptr;
    }

    errno = EINVAL;
    return MAP_FAILED;
}

void *v4l2_mmap(void *start, size_t length, int prot, int flags, int fd, int64_t offset)
{
    (void)start;
    (void)length;
    (void)prot;
    (void)flags;

    pthread_mutex_lock(&g_mutex);
    void *ptr = handle_mmap(start, length, prot, flags, fd, offset);
    pthread_mutex_unlock(&g_mutex);
    return ptr;
}

static int handle_munmap(void *start, size_t length)
{
    int i;

    (void)length;

    if (!g_initialized) {
        errno = EINVAL;
        return -1;
    }

    for (i = 0; i < g_buffer_count; i++) {
        if (g_buffers[i].ptr == start && g_buffers[i].ptr != MAP_FAILED) {
            if (g_buffers[i].queued) {
                LOG_DEBUG("v4l2_munmap: buffer %d is queued, cannot unmap", i);
                errno = EINVAL;
                return -1;
            }

            munmap(g_buffers[i].ptr, g_buffers[i].size);
            LOG_DEBUG("v4l2_munmap: unmapped buffer %d at %p", i, g_buffers[i].ptr);
            g_buffers[i].ptr = MAP_FAILED;
            return 0;
        }
    }

    errno = EINVAL;
    return -1;
}

int v4l2_munmap(void *start, size_t length)
{
    (void)length;

    pthread_mutex_lock(&g_mutex);
    int ret = handle_munmap(start, length);
    pthread_mutex_unlock(&g_mutex);
    return ret;
}
