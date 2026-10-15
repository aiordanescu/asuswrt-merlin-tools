#define _GNU_SOURCE
#include "wh_socket.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

uint64_t wh_now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t))
        return 0;
    return (uint64_t)t.tv_sec * WH_NSEC + (uint64_t)t.tv_nsec;
}
int wh_boot(uint8_t boot[16]) {
    char s[80];
    FILE *f = fopen("/proc/sys/kernel/random/boot_id", "r");
    if (!f)
        return -1;
    int ok = fgets(s, sizeof s, f) != NULL;
    fclose(f);
    return ok ? wh_uuid(s, boot) : -1;
}
int wh_random(uint8_t value[16]) {
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;
    size_t n = 0;
    while (n < 16) {
        ssize_t r = read(fd, value + n, 16 - n);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            break;
        n += (size_t)r;
    }
    close(fd);
    return n == 16 ? 0 : -1;
}
static int nonblock_socket(void) {
    int fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
    if (fd < 0)
        return -1;
    if (fcntl(fd, F_SETFD, FD_CLOEXEC) < 0 || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}
static int peer_allowed(int fd) {
#ifdef __linux__
    struct ucred c;
    socklen_t n = sizeof c;
    return !getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &c, &n) && (c.uid == 0 || c.uid == geteuid());
#else
    uid_t u;
    gid_t g;
    return !getpeereid(fd, &u, &g) && (u == 0 || u == geteuid());
#endif
}
static void peer_close(wh_server *s, int i) {
    if (s->peer[i].fd >= 0) {
        close(s->peer[i].fd);
        s->dropped++;
    }
    s->peer[i] = (wh_peer){.fd = -1};
}
int wh_server_open(wh_server *s, const char *path) {
    memset(s, 0, sizeof *s);
    s->listener = -1;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        s->peer[i].fd = -1;
    if (strlen(path) >= sizeof s->path) {
        errno = ENAMETOOLONG;
        return -1;
    }
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, path, strlen(path) + 1);
    struct stat st;
    if (!lstat(path, &st)) {
        if (!S_ISSOCK(st.st_mode) || st.st_uid != geteuid()) {
            errno = EEXIST;
            return -1;
        }
        int probe = nonblock_socket();
        if (probe < 0)
            return -1;
        int rc = connect(probe, (void *)&addr, sizeof addr), saved = errno;
        close(probe);
        if (rc == 0 || saved != ECONNREFUSED) {
            errno = EADDRINUSE;
            return -1;
        }
        if (unlink(path))
            return -1;
    } else if (errno != ENOENT)
        return -1;
    int fd = nonblock_socket();
    if (fd < 0)
        return -1;
    if (bind(fd, (void *)&addr, sizeof addr)) {
        int saved = errno;
        close(fd);
        errno = saved;
        return -1;
    }
    if (chmod(path, 0600) || listen(fd, WH_SUBSCRIBERS)) {
        int saved = errno;
        close(fd);
        unlink(path);
        errno = saved;
        return -1;
    }
    s->listener = fd;
    memcpy(s->path, path, strlen(path) + 1);
    return 0;
}
void wh_server_close(wh_server *s) {
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        peer_close(s, i);
    if (s->listener >= 0) {
        close(s->listener);
        s->listener = -1;
        if (s->path[0])
            unlink(s->path);
    }
}
static void transmit(wh_server *s, int i, uint64_t now) {
    wh_peer *p = &s->peer[i];
    if (p->fd < 0 || !s->length || p->sent == s->seq)
        return;
    ssize_t n = send(p->fd, s->frame, s->length, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (n == (ssize_t)s->length) {
        p->sent = s->seq;
        p->pending_since = 0;
        return;
    }
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
        if (!p->pending_since)
            p->pending_since = now;
        if (now - p->pending_since > WH_MAX_AGE_NS)
            peer_close(s, i);
    } else
        peer_close(s, i);
}
int wh_server_publish(wh_server *s, const wh_state *state, uint64_t now) {
    size_t n = wh_encode(s->frame, sizeof s->frame, state);
    if (!n)
        return -1;
    s->length = n;
    s->seq = state->seq;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        transmit(s, i, now);
    return 0;
}
void wh_server_fds(wh_server *s, struct pollfd f[WH_SERVER_FDS]) {
    f[0] = (struct pollfd){s->listener, POLLIN, 0};
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        f[i + 1] = (struct pollfd){
            s->peer[i].fd,
            (short)(POLLIN | ((s->length && s->peer[i].sent != s->seq) ? POLLOUT : 0)), 0};
}
void wh_server_step(wh_server *s, const struct pollfd f[WH_SERVER_FDS], uint64_t now) {
    /* Existing fds first: an accepted socket may reuse a just-closed number. */
    for (int i = 0; i < WH_SUBSCRIBERS; i++) {
        if (s->peer[i].fd < 0 || f[i + 1].fd != s->peer[i].fd)
            continue;
        if (f[i + 1].revents & (POLLIN | POLLERR | POLLHUP | POLLNVAL)) {
            peer_close(s, i);
            continue;
        }
        if (s->peer[i].pending_since && now - s->peer[i].pending_since > WH_MAX_AGE_NS) {
            peer_close(s, i);
            continue;
        }
        if (f[i + 1].revents & POLLOUT)
            transmit(s, i, now);
    }
    if (!(f[0].revents & POLLIN))
        return;
    for (int n = 0; n < 4; n++) {
        int fd = accept(s->listener, NULL, NULL);
        if (fd < 0)
            break;
        int slot = -1;
        for (int i = 0; i < WH_SUBSCRIBERS; i++)
            if (s->peer[i].fd < 0) {
                slot = i;
                break;
            }
        if (slot < 0 || !peer_allowed(fd) || fcntl(fd, F_SETFD, FD_CLOEXEC) < 0 ||
            fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
            close(fd);
            continue;
        }
        int bytes = 4096;
        setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &bytes, sizeof bytes);
        s->peer[slot] = (wh_peer){.fd = fd};
        s->accepted++;
        transmit(s, slot, now);
    }
}
void wh_client_init(wh_client *c, const char *path) {
    memset(c, 0, sizeof *c);
    c->fd = -1;
    c->backoff = 1;
    if (strlen(path) < sizeof c->path)
        memcpy(c->path, path, strlen(path) + 1);
    wh_boot(c->boot);
}
void wh_client_close(wh_client *c, uint64_t now) {
    if (c->fd >= 0)
        close(c->fd);
    c->fd = -1;
    c->connected = 0;
    c->retry_ns = now + (uint64_t)c->backoff * WH_NSEC;
    if (c->backoff < 8)
        c->backoff *= 2;
}
int wh_client_step(wh_client *c, uint64_t now) {
    int changed = 0;
    if (c->fd < 0 && c->path[0] && now >= c->retry_ns) {
        struct sockaddr_un a;
        memset(&a, 0, sizeof a);
        a.sun_family = AF_UNIX;
        memcpy(a.sun_path, c->path, strlen(c->path) + 1);
        c->fd = nonblock_socket();
        if (c->fd < 0 || connect(c->fd, (void *)&a, sizeof a) || !peer_allowed(c->fd)) {
            wh_client_close(c, now);
            return -1;
        }
        c->connected = 1; /* First valid packet chooses this connection's instance. */
    }
    if (c->fd < 0)
        return 0;
    for (int i = 0; i < 8; i++) {
        uint8_t b[WH_MAX_FRAME];
        ssize_t n = recv(c->fd, b, sizeof b, MSG_DONTWAIT | MSG_TRUNC);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR))
            break;
        if (n <= 0) {
            wh_client_close(c, now);
            return changed ? changed : -1;
        }
        wh_state s;
        if (n > (ssize_t)sizeof b || wh_decode(&s, b, (size_t)n) ||
            wh_validate(&s, c->boot, wh_now()) ||
            (c->connected == 2 && memcmp(s.instance, c->state.instance, 16)) ||
            (c->seen && !memcmp(s.instance, c->state.instance, 16) &&
             (s.seq < c->state.seq || (c->connected == 2 && s.seq == c->state.seq)))) {
            c->rejected++;
            wh_client_close(c, now);
            return -1;
        }
        c->state = s;
        c->seen = 1;
        c->connected = 2;
        c->backoff = 1;
        changed = 1;
    }
    return changed;
}
int wh_client_fresh(const wh_client *c, uint64_t now) {
    return c->connected == 2 && !wh_validate(&c->state, c->boot, now);
}
