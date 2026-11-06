// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

// libv4l2 plugin that publishes the frames unisrv captures from one camera
// device to a Unix socket, for capture-raw-mpp to encode. unisrv keeps owning
// the real camera.
//
// libv4l2 gives each device to the first plugin whose init() returns non-NULL,
// so claiming V4L2_CAMERA_PUBLISH_DEVICE hides it from Rockchip's
// libv4l-mplane.so, which that device needs. This plugin therefore loads
// libv4l-mplane.so itself and forwards every call to it. The file name must
// sort before libv4l-mplane.so.
//
// Publishing runs in unisrv's capture path and must not disturb it: at most
// one frame is in flight, anything that does not fit is dropped, and waits are
// bounded. Frames are published on DQBUF; a QBUF of the buffer still in flight
// waits briefly for its ack, so the camera does not overwrite a frame that is
// being encoded.

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <time.h>
#include <dlfcn.h>
#include <poll.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>
#include <linux/dma-buf.h>

#define RECONNECT_INTERVAL_MS 1000
#define STUCK_TIMEOUT_MS 2000

#define LOG_PREFIX "[v4l2-camera-publish] "
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

// Wire protocol, see apps/capture-raw-mpp/README.md in v4l2-mpp.
#define FRAME_MAGIC 0x31465256
enum { FRAME_SKIP = 0, FRAME_DMABUF = 1, FRAME_DATA = 2 };
#define FRAME_ACK_WANT 1u

typedef struct {
    uint32_t magic;
    uint32_t type;
    uint32_t seq;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t fourcc;
    uint32_t size;
} frame_hdr_t;

typedef struct {
    uint32_t seq;
    uint32_t flags;
} frame_ack_t;

static pthread_once_t cfg_once = PTHREAD_ONCE_INIT;
static bool cfg_active = false;
static char cfg_socket_path[256] = "";
static char cfg_device[256] = "";
static char cfg_next_plugin[256] = "/usr/lib/libv4l/plugins/libv4l-mplane.so";
static bool cfg_data_mode = false;
static int cfg_debug = 0;
static int cfg_timeout = 50;

// One per claimed fd. librockit calls QBUF and DQBUF from different threads.
typedef struct {
    void *next_lib;
    const struct libv4l_dev_ops *next_ops;
    void *next_priv;

    pthread_mutex_t lock;
    int sock;
    bool connect_failed;
    int64_t next_connect_ms;
    bool want;

    bool in_flight;
    uint32_t in_flight_seq;
    int in_flight_index;
    int64_t in_flight_since_ms;
    uint32_t seq;

    int buf_fd[VIDEO_MAX_FRAME];
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t fourcc;
} camera_t;

static int64_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

// Sets cfg_active when this process should publish a device: unisrv, with
// V4L2_CAMERA_PUBLISH_DEVICE set (by the lmd hook).
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

    env = getenv("V4L2_CAMERA_PUBLISH_DEVICE");
    if (!env || !*env)
        return;
    strncpy(cfg_device, env, sizeof(cfg_device) - 1);

    env = getenv("V4L2_CAMERA_PUBLISH_PATH");
    if (!env || !*env) {
        LOG_ERROR("V4L2_CAMERA_PUBLISH_PATH is not set");
        return;
    }
    if (strlen(env) >= sizeof(((struct sockaddr_un *)0)->sun_path)) {
        LOG_ERROR("V4L2_CAMERA_PUBLISH_PATH is too long");
        return;
    }
    strncpy(cfg_socket_path, env, sizeof(cfg_socket_path) - 1);

    env = getenv("V4L2_CAMERA_PUBLISH_DEBUG");
    if (env && atoi(env))
        cfg_debug = 1;

    env = getenv("V4L2_CAMERA_PUBLISH_MODE");
    if (env && *env) {
        if (strcasecmp(env, "data") == 0) {
            cfg_data_mode = true;
        } else if (strcasecmp(env, "dmabuf") != 0) {
            LOG_ERROR("Unsupported mode: %s", env);
            return;
        }
    }

    env = getenv("V4L2_CAMERA_PUBLISH_TIMEOUT");
    if (env && *env)
        cfg_timeout = atoi(env);

    env = getenv("V4L2_CAMERA_PUBLISH_NEXT_PLUGIN");
    if (env && *env) {
        memset(cfg_next_plugin, 0, sizeof(cfg_next_plugin));
        strncpy(cfg_next_plugin, env, sizeof(cfg_next_plugin) - 1);
    }

    cfg_active = true;
    LOG_INFO("Config: socket=%s device=%s mode=%s timeout=%d",
             cfg_socket_path, cfg_device, cfg_data_mode ? "data" : "dmabuf", cfg_timeout);
}

// librockit opens devices from several threads, so every caller must wait
// for the configuration to be complete.
static bool load_config(void)
{
    pthread_once(&cfg_once, config_init);
    return cfg_active;
}

// ---------------------------------------------------------------------------
// Next plugin
// ---------------------------------------------------------------------------

static void next_plugin_init(camera_t *cam, int fd)
{
    cam->next_lib = dlopen(cfg_next_plugin, RTLD_NOW | RTLD_LOCAL);
    if (!cam->next_lib) {
        LOG_ERROR("Failed to load %s: %s", cfg_next_plugin, dlerror());
        return;
    }

    const struct libv4l_dev_ops *ops = dlsym(cam->next_lib, "libv4l2_plugin");
    void *priv = ops && ops->init ? ops->init(fd) : NULL;
    if (!priv) {
        // Not a multi-planar device: libv4l2 would use the device directly.
        dlclose(cam->next_lib);
        cam->next_lib = NULL;
        return;
    }
    cam->next_ops = ops;
    cam->next_priv = priv;
}

static int next_ioctl(camera_t *cam, int fd, unsigned long int request, void *arg)
{
    if (cam->next_ops)
        return cam->next_ops->ioctl(cam->next_priv, fd, request, arg);
    return syscall(SYS_ioctl, fd, request, arg);
}

// ---------------------------------------------------------------------------
// Publishing. All of this runs with cam->lock held.
// ---------------------------------------------------------------------------

static void disconnect_socket(camera_t *cam)
{
    if (cam->sock >= 0) {
        close(cam->sock);
        cam->sock = -1;
    }
    cam->want = false;
    cam->in_flight = false;
    cam->in_flight_index = -1;
    cam->next_connect_ms = now_ms() + RECONNECT_INTERVAL_MS;
}

static void connect_socket(camera_t *cam)
{
    struct sockaddr_un addr;

    if (now_ms() < cam->next_connect_ms)
        return;
    cam->next_connect_ms = now_ms() + RECONNECT_INTERVAL_MS;

    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0)
        return;

    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, cfg_socket_path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        // The service may simply not be running, so only report this once.
        if (!cam->connect_failed)
            LOG_INFO("Waiting for %s: %s", cfg_socket_path, strerror(errno));
        cam->connect_failed = true;
        close(fd);
        return;
    }

    cam->sock = fd;
    cam->connect_failed = false;
    LOG_INFO("Connected to %s", cfg_socket_path);
}

static void drain_acks(camera_t *cam)
{
    while (cam->sock >= 0) {
        frame_ack_t ack;
        ssize_t n = recv(cam->sock, &ack, sizeof(ack), MSG_DONTWAIT);
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return;
        if (n != sizeof(ack)) {
            LOG_INFO("Disconnected from %s", cfg_socket_path);
            disconnect_socket(cam);
            return;
        }
        cam->want = ack.flags & FRAME_ACK_WANT;
        if (cam->in_flight && ack.seq == cam->in_flight_seq) {
            cam->in_flight = false;
            cam->in_flight_index = -1;
        }
    }
}

// Sends everything or disconnects: after a partial send the stream is unusable.
static bool send_data(camera_t *cam, const char *data, size_t size)
{
    int64_t deadline = now_ms() + cfg_timeout;

    while (size > 0) {
        ssize_t n = send(cam->sock, data, size, MSG_DONTWAIT | MSG_NOSIGNAL);
        if (n > 0) {
            data += n;
            size -= n;
            continue;
        }
        if (n < 0 && errno == EINTR)
            continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            int left = deadline - now_ms();
            struct pollfd pfd = { .fd = cam->sock, .events = POLLOUT };
            if (left > 0 && poll(&pfd, 1, left) >= 0)
                continue;
        }
        LOG_ERROR("Failed to send frame data, disconnecting");
        disconnect_socket(cam);
        return false;
    }
    return true;
}

// Sends the header, with the dma-buf attached if dmabuf_fd >= 0.
static bool send_header(camera_t *cam, const frame_hdr_t *hdr, int dmabuf_fd)
{
    struct iovec iov = { .iov_base = (void *)hdr, .iov_len = sizeof(*hdr) };
    struct msghdr msg = { .msg_iov = &iov, .msg_iovlen = 1 };
    char control[CMSG_SPACE(sizeof(int))];

    if (dmabuf_fd >= 0) {
        memset(control, 0, sizeof(control));
        msg.msg_control = control;
        msg.msg_controllen = sizeof(control);
        struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
        cmsg->cmsg_level = SOL_SOCKET;
        cmsg->cmsg_type = SCM_RIGHTS;
        cmsg->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(cmsg), &dmabuf_fd, sizeof(int));
    }

    ssize_t n = sendmsg(cam->sock, &msg, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (n == sizeof(*hdr))
        return true;
    // A full socket only costs this frame.
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR))
        return false;
    LOG_INFO("Disconnected from %s", cfg_socket_path);
    disconnect_socket(cam);
    return false;
}

static bool send_frame_data(camera_t *cam, int dmabuf_fd, size_t size)
{
    void *ptr = mmap(NULL, size, PROT_READ, MAP_SHARED, dmabuf_fd, 0);
    if (ptr == MAP_FAILED) {
        LOG_ERROR("mmap of dma-buf %d failed: %s", dmabuf_fd, strerror(errno));
        disconnect_socket(cam);
        return false;
    }

    struct dma_buf_sync sync = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ };
    ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, &sync);
    bool sent = send_data(cam, ptr, size);
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
    ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, &sync);
    munmap(ptr, size);
    return sent;
}

static void publish_frame(camera_t *cam, const struct v4l2_buffer *buf)
{
    if (buf->flags & V4L2_BUF_FLAG_ERROR)
        return;
    if (buf->memory != V4L2_MEMORY_DMABUF || buf->index >= VIDEO_MAX_FRAME)
        return;
    if (!cam->width || cam->buf_fd[buf->index] < 0)
        return;

    drain_acks(cam);
    if (cam->sock < 0)
        connect_socket(cam);
    if (cam->sock < 0)
        return;

    if (cam->in_flight) {
        if (now_ms() - cam->in_flight_since_ms >= STUCK_TIMEOUT_MS) {
            LOG_ERROR("No ack from %s, reconnecting", cfg_socket_path);
            disconnect_socket(cam);
        }
        return;
    }

    bool multiplanar = buf->type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
    uint32_t bytesused = multiplanar ? buf->m.planes[0].bytesused : buf->bytesused;
    if (bytesused == 0)
        return;
    int dmabuf_fd = cam->buf_fd[buf->index];
    frame_hdr_t hdr = {
        .magic = FRAME_MAGIC,
        .type = FRAME_SKIP,
        .seq = ++cam->seq,
        .width = cam->width,
        .height = cam->height,
        .stride = cam->stride,
        .fourcc = cam->fourcc,
        .size = bytesused,
    };

    // Without demand only tell the service a frame went by, so that it can
    // ask for the next one as soon as it has a client.
    if (cam->want)
        hdr.type = cfg_data_mode ? FRAME_DATA : FRAME_DMABUF;
    if (hdr.type == FRAME_SKIP)
        hdr.size = 0;

    if (!send_header(cam, &hdr, hdr.type == FRAME_DMABUF ? dmabuf_fd : -1))
        return;
    if (hdr.type == FRAME_DATA && !send_frame_data(cam, dmabuf_fd, hdr.size))
        return;

    cam->in_flight = true;
    cam->in_flight_seq = hdr.seq;
    cam->in_flight_index = hdr.type == FRAME_DMABUF ? (int)buf->index : -1;
    cam->in_flight_since_ms = now_ms();
    LOG_DEBUG("Published frame %u type %u: %u bytes", hdr.seq, hdr.type, hdr.size);
}

static void wait_for_ack(camera_t *cam, unsigned index)
{
    int64_t deadline = now_ms() + cfg_timeout;

    drain_acks(cam);
    while (cam->in_flight_index == (int)index) {
        int left = deadline - now_ms();
        struct pollfd pfd = { .fd = cam->sock, .events = POLLIN };
        if (left <= 0 || (poll(&pfd, 1, left) < 0 && errno != EINTR)) {
            // Still encoding: give the buffer back rather than stall capture.
            cam->in_flight_index = -1;
            LOG_DEBUG("Timeout waiting for ack of frame %u", cam->in_flight_seq);
            return;
        }
        drain_acks(cam);
    }
}

static void on_qbuf(camera_t *cam, const struct v4l2_buffer *buf)
{
    if (buf->memory != V4L2_MEMORY_DMABUF || buf->index >= VIDEO_MAX_FRAME)
        return;

    pthread_mutex_lock(&cam->lock);
    if (buf->type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE) {
        // A second plane would need its own fd, which the protocol does not carry.
        cam->buf_fd[buf->index] = buf->length == 1 ? buf->m.planes[0].m.fd : -1;
    } else {
        cam->buf_fd[buf->index] = buf->m.fd;
    }
    wait_for_ack(cam, buf->index);
    pthread_mutex_unlock(&cam->lock);
}

static void on_streamon(camera_t *cam, int fd, int type)
{
    struct v4l2_format fmt = { .type = type };

    if (next_ioctl(cam, fd, VIDIOC_G_FMT, &fmt) < 0) {
        LOG_ERROR("G_FMT failed: %s, not publishing", strerror(errno));
        return;
    }

    pthread_mutex_lock(&cam->lock);
    cam->width = 0;
    if (type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE) {
        if (fmt.fmt.pix_mp.num_planes == 1) {
            cam->width = fmt.fmt.pix_mp.width;
            cam->height = fmt.fmt.pix_mp.height;
            cam->stride = fmt.fmt.pix_mp.plane_fmt[0].bytesperline;
            cam->fourcc = fmt.fmt.pix_mp.pixelformat;
        } else {
            LOG_ERROR("%u planes are not supported, not publishing", fmt.fmt.pix_mp.num_planes);
        }
    } else if (type == V4L2_BUF_TYPE_VIDEO_CAPTURE) {
        cam->width = fmt.fmt.pix.width;
        cam->height = fmt.fmt.pix.height;
        cam->stride = fmt.fmt.pix.bytesperline;
        cam->fourcc = fmt.fmt.pix.pixelformat;
    }
    if (cam->width)
        LOG_INFO("STREAMON %s: %ux%u stride %u %.4s", cfg_device, cam->width, cam->height,
                 cam->stride, (const char *)&cam->fourcc);
    pthread_mutex_unlock(&cam->lock);
}

static void on_buffers_gone(camera_t *cam)
{
    pthread_mutex_lock(&cam->lock);
    // Closing the connection also makes the service drop its buffer references.
    disconnect_socket(cam);
    cam->next_connect_ms = 0;
    for (int i = 0; i < VIDEO_MAX_FRAME; i++)
        cam->buf_fd[i] = -1;
    pthread_mutex_unlock(&cam->lock);
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

static void *plugin_init(int fd)
{
    char link[64], path[256];

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

    pthread_mutex_init(&cam->lock, NULL);
    cam->sock = -1;
    cam->in_flight_index = -1;
    for (int i = 0; i < VIDEO_MAX_FRAME; i++)
        cam->buf_fd[i] = -1;
    next_plugin_init(cam, fd);

    LOG_INFO("init(%s): fd %d publishing to %s", path, fd, cfg_socket_path);
    return cam;
}

static void plugin_close(void *priv)
{
    camera_t *cam = priv;

    LOG_DEBUG("close(%s)", cfg_device);
    disconnect_socket(cam);
    if (cam->next_ops) {
        if (cam->next_ops->close)
            cam->next_ops->close(cam->next_priv);
        dlclose(cam->next_lib);
    }
    pthread_mutex_destroy(&cam->lock);
    free(cam);
}

static int plugin_ioctl(void *priv, int fd, unsigned long int request, void *arg)
{
    camera_t *cam = priv;
    unsigned int req = request & 0xffffffff;

    if (req == VIDIOC_QBUF)
        on_qbuf(cam, arg);

    int ret = next_ioctl(cam, fd, request, arg);
    if (ret < 0)
        return ret;

    switch (req) {
    case VIDIOC_DQBUF:
        pthread_mutex_lock(&cam->lock);
        publish_frame(cam, arg);
        pthread_mutex_unlock(&cam->lock);
        break;
    case VIDIOC_STREAMON:
        on_streamon(cam, fd, *(const int *)arg);
        break;
    case VIDIOC_STREAMOFF:
    case VIDIOC_REQBUFS:
        on_buffers_gone(cam);
        break;
    default:
        break;
    }
    return ret;
}

static ssize_t plugin_read(void *priv, int fd, void *buffer, size_t n)
{
    camera_t *cam = priv;

    if (cam->next_ops && cam->next_ops->read)
        return cam->next_ops->read(cam->next_priv, fd, buffer, n);
    return syscall(SYS_read, fd, buffer, n);
}

static ssize_t plugin_write(void *priv, int fd, const void *buffer, size_t n)
{
    camera_t *cam = priv;

    if (cam->next_ops && cam->next_ops->write)
        return cam->next_ops->write(cam->next_priv, fd, buffer, n);
    return syscall(SYS_write, fd, buffer, n);
}

__attribute__((visibility("default")))
const struct libv4l_dev_ops libv4l2_plugin = {
    .init = plugin_init,
    .close = plugin_close,
    .ioctl = plugin_ioctl,
    .read = plugin_read,
    .write = plugin_write,
};
