// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

// libv4l2 plugin that serves one camera device in unisrv from a Unix socket
// instead of the real camera.
//
// libv4l2 offers every device it opens to the plugins in
// /usr/lib/libv4l/plugins in name order; the first plugin whose init()
// returns non-NULL gets all ioctls of that fd. This plugin only claims
// V4L2_CAMERA_SOCKET_DEVICE in unisrv, so every other device goes on to the
// next plugin (Rockchip's libv4l-mplane.so) as on stock firmware. The file
// name must sort before libv4l-mplane.so.
//
// unisrv captures through librockit, which uses libv4l2 with DMABUF buffers
// it allocates itself; frames are read straight into the queued buffers.

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <poll.h>
#include <pthread.h>
#include <linux/videodev2.h>
#include <linux/dma-buf.h>

#define MAX_BUFFERS 4
#define MAX_BUFFER_SIZE (4 * 1024 * 1024)

#define FOURCC_ARGS(f) (f) & 0xff, ((f) >> 8) & 0xff, ((f) >> 16) & 0xff, ((f) >> 24) & 0xff
#define FOURCC_FMT "%c%c%c%c"

#define LOG_PREFIX "[v4l2-camera-socket] "
#define LOG_DEBUG(fmt, ...) do { if (cfg_debug) fprintf(stderr, LOG_PREFIX "DEBUG[%d]: " fmt "\n", getpid(), ##__VA_ARGS__); } while(0)
#define LOG_INFO(fmt, ...) fprintf(stderr, LOG_PREFIX "INFO[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)
#define LOG_ERROR(fmt, ...) fprintf(stderr, LOG_PREFIX "ERROR[%d]: " fmt "\n", getpid(), ##__VA_ARGS__)

// The plugin API of libv4l2 (lib/include/libv4l-plugin.h in v4l-utils).
struct libv4l_dev_ops {
    void *(*init)(int fd);
    void (*close)(void *dev_ops_priv);
    int (*ioctl)(void *dev_ops_priv, int fd, unsigned long int request, void *arg);
    ssize_t (*read)(void *dev_ops_priv, int fd, void *buffer, size_t n);
    ssize_t (*write)(void *dev_ops_priv, int fd, const void *buffer, size_t n);
    void (*reserved1)(void);
    void (*reserved2)(void);
    void (*reserved3)(void);
    void (*reserved4)(void);
    void (*reserved5)(void);
    void (*reserved6)(void);
    void (*reserved7)(void);
};

static pthread_once_t cfg_once = PTHREAD_ONCE_INIT;
static bool cfg_active = false;
static char cfg_socket_path[256] = "";
static char cfg_device[256] = "";
static int cfg_width = 1920;
static int cfg_height = 1080;
static uint32_t cfg_format = V4L2_PIX_FMT_NV12;
static int cfg_debug = 0;
static int cfg_socket_timeout = 1000;

typedef struct {
    void *ptr;
    size_t size;
    size_t used;
    int dmabuf_fd;
    bool queued;
} buffer_t;

// One per claimed fd. The fd itself is turned into the read end of a pipe
// that holds the indexes of queued buffers, so poll() works as on a device.
typedef struct {
    int pipe_wr;
    bool streaming;
    int buffer_count;
    buffer_t buffers[MAX_BUFFERS];
} camera_t;

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

static void free_buffers(camera_t *cam)
{
    for (int i = 0; i < MAX_BUFFERS; i++) {
        buffer_t *b = &cam->buffers[i];
        if (b->ptr && b->ptr != MAP_FAILED)
            munmap(b->ptr, b->size);
        b->ptr = MAP_FAILED;
        b->size = 0;
        b->used = 0;
        b->dmabuf_fd = -1;
        b->queued = false;
    }
    cam->buffer_count = 0;
}

// Sets cfg_active when this process should serve a device: unisrv, with
// V4L2_CAMERA_SOCKET_DEVICE set (by the lmd hook in paxx12 mode).
static void config_init(void)
{
    const char *env;

    char self_path[256];
    ssize_t len = readlink("/proc/self/exe", self_path, sizeof(self_path) - 1);
    if (len < 0)
        return;
    self_path[len] = '\0';
    const char *basename = strrchr(self_path, '/');
    basename = basename ? basename + 1 : self_path;
    if (strcmp(basename, "unisrv") != 0)
        return;

    env = getenv("V4L2_CAMERA_SOCKET_DEVICE");
    if (!env || !*env)
        return;
    strncpy(cfg_device, env, sizeof(cfg_device) - 1);

    env = getenv("V4L2_CAMERA_SOCKET_DEBUG");
    if (env && atoi(env))
        cfg_debug = 1;

    env = getenv("V4L2_CAMERA_SOCKET_PATH");
    if (env && *env)
        strncpy(cfg_socket_path, env, sizeof(cfg_socket_path) - 1);

    env = getenv("V4L2_CAMERA_SOCKET_WIDTH");
    if (env)
        cfg_width = atoi(env);

    env = getenv("V4L2_CAMERA_SOCKET_HEIGHT");
    if (env)
        cfg_height = atoi(env);

    env = getenv("V4L2_CAMERA_SOCKET_FORMAT");
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
            return;
        }
    }

    env = getenv("V4L2_CAMERA_SOCKET_TIMEOUT");
    if (env)
        cfg_socket_timeout = atoi(env);

    cfg_active = true;
    LOG_INFO("Config: socket=%s device=%s width=%d height=%d format=" FOURCC_FMT " timeout=%d",
             cfg_socket_path, cfg_device, cfg_width, cfg_height, FOURCC_ARGS(cfg_format), cfg_socket_timeout);
}

// librockit opens devices from several threads, so every caller must wait
// for the configuration to be complete.
static bool load_config(void)
{
    pthread_once(&cfg_once, config_init);
    return cfg_active;
}

// ---------------------------------------------------------------------------
// Frame source
// ---------------------------------------------------------------------------

static int connect_to_socket(void)
{
    struct sockaddr_un addr;
    int fd;

    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        LOG_ERROR("Failed to create socket: %s", strerror(errno));
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, cfg_socket_path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        LOG_ERROR("Failed to connect to %s: %s", cfg_socket_path, strerror(errno));
        close(fd);
        return -1;
    }
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
        if (pfd.revents & POLLHUP && !(pfd.revents & POLLIN))
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

    buf->used = n;
    LOG_DEBUG("Fetched frame: %zd bytes", n);
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

// ---------------------------------------------------------------------------
// ioctls, including the ones libv4l2 and libv4lconvert probe on open
// ---------------------------------------------------------------------------

static int handle_querycap(struct v4l2_capability *cap)
{
    memset(cap, 0, sizeof(*cap));
    strncpy((char *)cap->driver, "v4l2-camera-socket", sizeof(cap->driver) - 1);
    snprintf((char *)cap->card, sizeof(cap->card), "Socket camera %.17s", cfg_device);
    strncpy((char *)cap->bus_info, "platform:v4l2-camera-socket", sizeof(cap->bus_info) - 1);
    cap->version = 0x00050400;
    cap->device_caps = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING;
    cap->capabilities = cap->device_caps | V4L2_CAP_DEVICE_CAPS;
    return 0;
}

static int handle_enum_fmt(struct v4l2_fmtdesc *fmt)
{
    if (fmt->index > 0 || fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;

    memset(fmt, 0, sizeof(*fmt));
    fmt->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt->pixelformat = cfg_format;
    if (is_compressed())
        fmt->flags = V4L2_FMT_FLAG_COMPRESSED;
    snprintf((char *)fmt->description, sizeof(fmt->description), FOURCC_FMT, FOURCC_ARGS(cfg_format));
    return 0;
}

static int handle_enum_framesizes(struct v4l2_frmsizeenum *fsize)
{
    if (fsize->index > 0 || fsize->pixel_format != cfg_format)
        return -EINVAL;
    fsize->type = V4L2_FRMSIZE_TYPE_DISCRETE;
    fsize->discrete.width = cfg_width;
    fsize->discrete.height = cfg_height;
    return 0;
}

static int fill_fmt(struct v4l2_format *fmt)
{
    if (fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;
    if (image_size() > MAX_BUFFER_SIZE) {
        LOG_ERROR("sizeimage overflow");
        return -EINVAL;
    }
    memset(&fmt->fmt.pix, 0, sizeof(fmt->fmt.pix));
    fmt->fmt.pix.width = cfg_width;
    fmt->fmt.pix.height = cfg_height;
    fmt->fmt.pix.pixelformat = cfg_format;
    fmt->fmt.pix.field = V4L2_FIELD_NONE;
    fmt->fmt.pix.bytesperline = bytes_per_line();
    fmt->fmt.pix.sizeimage = image_size();
    fmt->fmt.pix.colorspace = V4L2_COLORSPACE_JPEG;
    return 0;
}

static int handle_s_fmt(struct v4l2_format *fmt)
{
    if (fmt->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;
    if (fmt->fmt.pix.width != (unsigned)cfg_width ||
        fmt->fmt.pix.height != (unsigned)cfg_height ||
        fmt->fmt.pix.pixelformat != cfg_format) {
        LOG_ERROR("S_FMT: requested %ux%u " FOURCC_FMT " but configured %dx%d " FOURCC_FMT,
                  fmt->fmt.pix.width, fmt->fmt.pix.height, FOURCC_ARGS(fmt->fmt.pix.pixelformat),
                  cfg_width, cfg_height, FOURCC_ARGS(cfg_format));
        return -EINVAL;
    }
    return fill_fmt(fmt);
}

static int handle_reqbufs(camera_t *cam, struct v4l2_requestbuffers *req)
{
    if (req->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;
    // librockit allocates the buffers and queues them as dma-buf fds
    if (req->memory != V4L2_MEMORY_DMABUF) {
        LOG_ERROR("REQBUFS: unsupported memory %u", req->memory);
        return -EINVAL;
    }

    free_buffers(cam);
    if (req->count == 0)
        return 0;
    if (req->count > MAX_BUFFERS)
        req->count = MAX_BUFFERS;

    for (unsigned i = 0; i < req->count; i++)
        cam->buffers[i].size = image_size();
    cam->buffer_count = req->count;
    LOG_DEBUG("REQBUFS: %u buffers of %zu bytes", req->count, image_size());
    return 0;
}

static int handle_qbuf(camera_t *cam, struct v4l2_buffer *buf)
{
    if (buf->type != V4L2_BUF_TYPE_VIDEO_CAPTURE || buf->index >= (unsigned)cam->buffer_count)
        return -EINVAL;

    buffer_t *b = &cam->buffers[buf->index];
    if (b->queued)
        return -EINVAL;
    if (b->dmabuf_fd != buf->m.fd) {
        if (buf->length && buf->length < b->size) {
            LOG_ERROR("QBUF: dma-buf %d is %u bytes, need %zu", buf->m.fd, buf->length, b->size);
            return -EINVAL;
        }
        if (b->ptr != MAP_FAILED)
            munmap(b->ptr, b->size);
        b->ptr = mmap(NULL, b->size, PROT_READ | PROT_WRITE, MAP_SHARED, buf->m.fd, 0);
        b->dmabuf_fd = b->ptr != MAP_FAILED ? buf->m.fd : -1;
        if (b->ptr == MAP_FAILED) {
            LOG_ERROR("QBUF: mmap of dma-buf %d failed: %s", buf->m.fd, strerror(errno));
            return -EINVAL;
        }
    }

    b->queued = true;
    if (cam->streaming) {
        uint8_t idx = buf->index;
        if (write(cam->pipe_wr, &idx, 1) != 1) {
            b->queued = false;
            return -EIO;
        }
    }
    return 0;
}

static int handle_dqbuf(camera_t *cam, int fd, struct v4l2_buffer *buf)
{
    uint8_t idx;

    if (buf->type != V4L2_BUF_TYPE_VIDEO_CAPTURE || !cam->streaming)
        return -EINVAL;

    ssize_t n = read(fd, &idx, 1);
    if (n != 1)
        return (n < 0 && errno == EAGAIN) ? -EAGAIN : -EIO;
    if (idx >= cam->buffer_count || !cam->buffers[idx].queued)
        return -EIO;

    buffer_t *b = &cam->buffers[idx];
    if (fetch_frame(b) < 0) {
        // Keep the buffer queued so capture can continue.
        if (write(cam->pipe_wr, &idx, 1) != 1)
            b->queued = false;
        return -EIO;
    }
    b->queued = false;

    buf->index = idx;
    buf->memory = V4L2_MEMORY_DMABUF;
    buf->bytesused = b->used;
    buf->length = b->size;
    buf->m.fd = b->dmabuf_fd;
    buf->field = V4L2_FIELD_NONE;
    buf->flags = V4L2_BUF_FLAG_DONE | V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    buf->timestamp.tv_sec = ts.tv_sec;
    buf->timestamp.tv_usec = ts.tv_nsec / 1000;
    return 0;
}

static int handle_streamon(camera_t *cam, const int *type)
{
    if (*type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;
    if (cam->streaming)
        return 0;

    for (int i = 0; i < cam->buffer_count; i++) {
        if (!cam->buffers[i].queued)
            continue;
        uint8_t idx = i;
        if (write(cam->pipe_wr, &idx, 1) != 1)
            return -EIO;
    }
    cam->streaming = true;
    LOG_INFO("STREAMON %s", cfg_device);
    return 0;
}

static int handle_streamoff(camera_t *cam, int fd, const int *type)
{
    uint8_t idx;

    if (*type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
        return -EINVAL;

    cam->streaming = false;
    while (read(fd, &idx, 1) == 1)
        ;
    for (int i = 0; i < cam->buffer_count; i++)
        cam->buffers[i].queued = false;
    LOG_INFO("STREAMOFF %s", cfg_device);
    return 0;
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

static void *plugin_init(int fd)
{
    char link[64], path[256];
    int pipefd[2];

    if (!load_config())
        return NULL;

    snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    ssize_t len = readlink(link, path, sizeof(path) - 1);
    if (len < 0)
        return NULL;
    path[len] = '\0';
    if (strcmp(path, cfg_device) != 0)
        return NULL;

    camera_t *cam = calloc(1, sizeof(*cam));
    if (!cam)
        return NULL;
    free_buffers(cam);

    // Replace the real device behind fd with a pipe holding the indexes of
    // queued buffers, so that poll() on fd and closing it work as expected.
    int flags = fcntl(fd, F_GETFD);
    if (pipe2(pipefd, O_NONBLOCK | O_CLOEXEC) < 0 || dup2(pipefd[0], fd) < 0) {
        LOG_ERROR("init(%s): pipe failed: %s", path, strerror(errno));
        free(cam);
        return NULL;
    }
    close(pipefd[0]);
    if (flags >= 0)
        fcntl(fd, F_SETFD, flags);
    cam->pipe_wr = pipefd[1];

    LOG_INFO("init(%s): fd %d served from %s", path, fd, cfg_socket_path);
    return cam;
}

static void plugin_close(void *priv)
{
    camera_t *cam = priv;

    LOG_DEBUG("close(%s)", cfg_device);
    free_buffers(cam);
    close(cam->pipe_wr);
    free(cam);
}

static int plugin_ioctl(void *priv, int fd, unsigned long int request, void *arg)
{
    camera_t *cam = priv;
    int ret;

    switch (request & 0xffffffff) {
    case VIDIOC_QUERYCAP:
        ret = handle_querycap(arg);
        break;
    case VIDIOC_ENUM_FMT:
        ret = handle_enum_fmt(arg);
        break;
    case VIDIOC_ENUM_FRAMESIZES:
        ret = handle_enum_framesizes(arg);
        break;
    case VIDIOC_G_FMT:
    case VIDIOC_TRY_FMT:
        ret = fill_fmt(arg);
        break;
    case VIDIOC_S_FMT:
        ret = handle_s_fmt(arg);
        break;
    case VIDIOC_REQBUFS:
        ret = handle_reqbufs(cam, arg);
        break;
    case VIDIOC_QBUF:
        ret = handle_qbuf(cam, arg);
        break;
    case VIDIOC_DQBUF:
        ret = handle_dqbuf(cam, fd, arg);
        break;
    case VIDIOC_STREAMON:
        ret = handle_streamon(cam, arg);
        break;
    case VIDIOC_STREAMOFF:
        ret = handle_streamoff(cam, fd, arg);
        break;
    default:
        LOG_DEBUG("ioctl 0x%lx not supported", request & 0xffffffff);
        ret = -ENOTTY;
        break;
    }

    if (ret < 0) {
        errno = -ret;
        return -1;
    }
    return ret;
}

static ssize_t plugin_read(void *priv, int fd, void *buffer, size_t n)
{
    (void)priv;
    (void)fd;
    (void)buffer;
    (void)n;
    errno = EINVAL;
    return -1;
}

static ssize_t plugin_write(void *priv, int fd, const void *buffer, size_t n)
{
    (void)priv;
    (void)fd;
    (void)buffer;
    (void)n;
    errno = EINVAL;
    return -1;
}

__attribute__((visibility("default")))
const struct libv4l_dev_ops libv4l2_plugin = {
    .init = plugin_init,
    .close = plugin_close,
    .ioctl = plugin_ioctl,
    .read = plugin_read,
    .write = plugin_write,
};
