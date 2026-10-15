#define _GNU_SOURCE
#include "wh_socket.h"
#include "ww_core.h"
#include "ww_probe.h"
#include "ww_worker.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/rtnetlink.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    int enable, active, route[2];
    uint32_t ip, gateway, target[2];
    char ifname[16];
} fact;
typedef struct {
    unsigned magic;
    fact wan[2];
    int failed, action_error, operation, wanted, restore, acted;
    uint64_t started, completed, generation;
} view;
typedef struct {
    char adapter[256], runtime[160];
    int operation, wanted, restore, pinned, p[2], a[2], mask[2];
    ww_flags flags[2];
    uint64_t sampled, generation;
    fact expected[2];
    int close_fds[32], nclose;
} context;
static volatile sig_atomic_t stopping;
static int log_fd = -1;
/* Let the firmware logger supply its local timestamp. Static musl and the
 * firmware libc may use different timezone files. Logging must never delay
 * probe deadlines if syslogd is unavailable or its receive queue is full. */
static void monitor_log(int priority, const char *format, ...) {
    if (log_fd < 0) {
        struct sockaddr_un address = {.sun_family = AF_UNIX};
        strcpy(address.sun_path, "/dev/log");
        log_fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        if (log_fd < 0)
            return;
        if (connect(log_fd, (void *)&address, sizeof address)) {
            close(log_fd);
            log_fd = -1;
            return;
        }
    }
    char message[640];
    int prefix = snprintf(message, sizeof message, "<%d>wan-watchdog[%ld]: ", LOG_DAEMON | priority,
                          (long)getpid());
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(message + prefix, sizeof message - (size_t)prefix, format, arguments);
    va_end(arguments);
    if (send(log_fd, message, strlen(message), MSG_DONTWAIT | MSG_NOSIGNAL) < 0 &&
        errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
        close(log_fd);
        log_fd = -1;
    }
}
static void on_signal(int sig) {
    (void)sig;
    stopping = 1;
}
static void fatal(const char *s) {
    perror(s);
    exit(1);
}
static uint64_t boot_time(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_BOOTTIME, &t))
        fatal("CLOCK_BOOTTIME");
    return (uint64_t)t.tv_sec * WH_NSEC + (uint64_t)t.tv_nsec;
}
static int address(const char *s, uint32_t *v) {
    return inet_pton(AF_INET, s, v) == 1 ? 0 : -1;
}
static int invoke(context *c, const char *phase, view *v) {
    int pipefd[2];
    if (pipe2(pipefd, O_CLOEXEC))
        return -1;
    pid_t pid = fork();
    if (pid < 0) {
        close(pipefd[0]);
        close(pipefd[1]);
        return -1;
    }
    if (!pid) {
        close(pipefd[0]);
        if (dup2(pipefd[1], STDOUT_FILENO) < 0)
            _exit(125);
        close(pipefd[1]);
        char arg[8][16];
        int values[8] = {c->wanted, c->restore, c->p[0],    c->p[1],
                         c->a[0],   c->a[1],    c->mask[0], c->mask[1]};
        for (int i = 0; i < 8; i++)
            snprintf(arg[i], sizeof arg[i], "%d", values[i]);
        execl("/bin/sh", "sh", c->adapter, phase, arg[0], arg[1], arg[2], arg[3], arg[4], arg[5],
              arg[6], arg[7], (char *)NULL);
        _exit(127);
    }
    close(pipefd[1]);
    FILE *f = fdopen(pipefd[0], "r");
    if (!f) {
        close(pipefd[0]);
        kill(pid, SIGKILL);
        waitpid(pid, NULL, 0);
        return -1;
    }
    char row[512];
    int mask = 0, end = 0, have_route = 0;
    while (fgets(row, sizeof row, f)) {
        int failed;
        if (sscanf(row, "R %d", &failed) == 1) {
            if (failed >= -1 && failed <= 1) {
                v->failed = failed;
                have_route = 1;
            }
            continue;
        }
        if (!strcmp(row, "END\n")) {
            end = 1;
            continue;
        }
        int unit, en, active, a, b;
        char iface[16], ip[32], gw[32], ta[32], tb[32];
        if (sscanf(row, "F %d %d %15s %31s %31s %d %d %d %31s %31s", &unit, &en, iface, ip, gw,
                   &active, &a, &b, ta, tb) != 10)
            continue;
        if (unit < 0 || unit > 1 || mask & (1 << unit) || en < -1 || en > 1 || active < 0 ||
            active > 1 || a < -1 || a > 1 || b < -1 || b > 1) {
            mask = -1;
            break;
        }
        fact *w = &v->wan[unit];
        memset(w, 0, sizeof *w);
        w->enable = en;
        w->active = active;
        w->route[0] = a;
        w->route[1] = b;
        if (strcmp(iface, "-"))
            memcpy(w->ifname, iface, strlen(iface) + 1);
        if (address(ip, &w->ip) || address(gw, &w->gateway) || address(ta, &w->target[0]) ||
            address(tb, &w->target[1])) {
            mask = -1;
            break;
        }
        mask |= 1 << unit;
    }
    int io = ferror(f);
    fclose(f);
    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR)
            return -1;
    }
    if (io || !WIFEXITED(status) || WEXITSTATUS(status))
        return -1;
    return mask == 3 && end && have_route ? 0 : -1;
}
static int collect(void *arg, void *result, size_t capacity) {
    context *c = arg;
    view *v = result;
    if (capacity != sizeof *v)
        return -1;
    for (int i = 0; i < c->nclose; i++)
        if (c->close_fds[i] >= 0)
            close(c->close_fds[i]);
    memset(v, 0, sizeof *v);
    v->started = wh_now();
    v->generation = c->generation;
    v->operation = c->operation;
    v->wanted = c->wanted;
    v->restore = c->restore;
    v->acted = c->operation == 2 &&
               (c->pinned || c->restore || (c->wanted >= 0 && c->flags[1 - c->wanted].acceptable));
    if (c->operation == 2) {
        for (int u = 0; u < 2; u++) {
            char p[256];
            snprintf(p, sizeof p, "%s/health_wan%d", c->runtime, u);
            FILE *f = fopen(p, "w");
            if (!f)
                return -1;
            ww_flags *h = &c->flags[u];
            fprintf(f, "%d %d %d %s %d %d %d %d\n", h->bad, h->acceptable, h->recovered,
                    wh_reason_name((unsigned)h->reason), h->loss[0], h->loss[1], h->samples[0],
                    h->samples[1]);
            if (fclose(f))
                return -1;
        }
        char seconds[32];
        snprintf(seconds, sizeof seconds, "%llu", (unsigned long long)(c->sampled / WH_NSEC));
        setenv("WW_SAMPLE_SECONDS", seconds, 1);
        for (int u = 0; u < 2; u++) {
            fact *f = &c->expected[u];
            char ip[INET_ADDRSTRLEN], gw[INET_ADDRSTRLEN], name[32], value[160];
            if (!inet_ntop(AF_INET, &f->ip, ip, sizeof ip) ||
                !inet_ntop(AF_INET, &f->gateway, gw, sizeof gw))
                return -1;
            snprintf(name, sizeof name, "WW_EXPECT%d", u);
            snprintf(value, sizeof value, "%d:%s:%s:%s", f->enable, ip, gw, f->ifname);
            setenv(name, value, 1);
        }
        for (int t = 0; t < 2; t++) {
            char name[32], value[INET_ADDRSTRLEN];
            if (!inet_ntop(AF_INET, &c->expected[0].target[t], value, sizeof value))
                return -1;
            snprintf(name, sizeof name, "WW_TARGET%d", t);
            setenv(name, value, 1);
        }
    }
    if (invoke(c, c->operation == 1 ? "init" : c->operation == 2 ? "apply" : "inspect", v)) {
        v->action_error = 1;
        if (invoke(c, "inspect", v))
            return -1;
    }
    v->completed = wh_now();
    v->magic = 0x57574e31;
    return 0;
}
static int route_events(void) {
    int fd = socket(AF_NETLINK, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, NETLINK_ROUTE);
    if (fd < 0)
        return -1;
    struct sockaddr_nl a = {.nl_family = AF_NETLINK,
                            .nl_groups = RTMGRP_LINK | RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE};
    if (bind(fd, (void *)&a, sizeof a)) {
        close(fd);
        return -1;
    }
    return fd;
}
static void status_file(const char *runtime, const wh_state *s, const wh_server *server, int shadow,
                        unsigned ok, unsigned failed, uint64_t jitter) {
    char path[256], temp[256];
    snprintf(path, sizeof path, "%s/status", runtime);
    snprintf(temp, sizeof temp, "%s/status.new", runtime);
    FILE *f = fopen(temp, "w");
    if (!f)
        return;
    unsigned peers = 0;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        peers += server->peer[i].fd >= 0;
    fprintf(
        f,
        "native=1 shadow=%d uptime_seconds=%llu mode=%s seq=%llu progress=%llu publisher_ready=%d "
        "subscribers=%u refresh_ok=%u refresh_failed=%u max_dispatch_delay_us=%llu "
        "routing_error=%u desired=%d applied=%d desired_generation=%llu applied_generation=%llu\n",
        shadow, (unsigned long long)(wh_now() / WH_NSEC), s->pinned ? "pinned" : "whole_lan",
        (unsigned long long)s->seq, (unsigned long long)s->progress, server->listener >= 0, peers,
        ok, failed, (unsigned long long)(jitter / 1000), s->routing_error, s->desired_failed,
        s->applied_failed, (unsigned long long)s->desired_generation,
        (unsigned long long)s->applied_generation);
    for (int u = 0; u < 2; u++) {
        const wh_wan *w = &s->wan[u];
        fprintf(f,
                "WAN%d %s loss=%u%%/%u%% samples=%u/%u bad=%u acceptable=%u eligible=%u "
                "excluded=%u recovery=%u measured_ns=%llu\n",
                u, wh_reason_name(w->reason),
                w->samples[0] ? (100 * w->lost[0] + w->samples[0] - 1) / w->samples[0] : 100,
                w->samples[1] ? (100 * w->lost[1] + w->samples[1] - 1) / w->samples[1] : 100,
                w->samples[0], w->samples[1], w->bad, w->acceptable, w->eligible, w->excluded,
                w->recovery, (unsigned long long)w->measured_ns);
    }
    if (!fclose(f))
        rename(temp, path);
}
static void cache(const char *runtime, ww_core *core, const uint8_t boot[16], int load) {
    char path[256], temp[256];
    snprintf(path, sizeof path, "%s/native-health.cache", runtime);
    snprintf(temp, sizeof temp, "%s/native-health.cache.new", runtime);
    uint8_t b[WH_MAX_FRAME];
    wh_state s;
    memset(&s, 0, sizeof s);
    if (load) {
        FILE *f = fopen(path, "r");
        if (!f)
            return;
        size_t n = fread(b, 1, sizeof b, f);
        int bad = ferror(f);
        fclose(f);
        if (bad || wh_decode(&s, b, n) || memcmp(s.boot, boot, 16))
            return;
        for (int u = 0; u < 2; u++)
            core->wan[u].excluded = (int)s.wan[u].excluded;
    } else {
        memcpy(s.boot, boot, 16);
        s.desired_failed = s.applied_failed = -1;
        for (int u = 0; u < 2; u++)
            s.wan[u] = core->wan[u].health;
        size_t n = wh_encode(b, sizeof b, &s);
        FILE *f = fopen(temp, "w");
        if (!f)
            return;
        int bad = fwrite(b, 1, n, f) != n;
        if (fclose(f))
            bad = 1;
        if (!bad)
            rename(temp, path);
    }
}
static void publish(wh_server *server, wh_state *state, ww_core *core, uint64_t now) {
    for (int u = 0; u < 2; u++)
        state->wan[u] = core->wan[u].health;
    state->seq++;
    state->published_ns = now;
    if (server->listener >= 0 && wh_server_publish(server, state, now))
        fprintf(stderr, "health encoding failed\n");
}
static int safe_path(const char *path) {
    return path[0] == '/' && strlen(path) < 150 && !strstr(path, "..") && !strpbrk(path, " \t\r\n");
}
static unsigned env_number(const char *name, unsigned fallback, unsigned min, unsigned max) {
    const char *s = getenv(name);
    if (!s || !*s)
        return fallback;
    unsigned long long n = 0;
    for (const char *p = s; *p; p++) {
        if (*p < '0' || *p > '9')
            goto invalid;
        n = n * 10 + (unsigned)(*p - '0');
        if (n > max)
            goto invalid;
    }
    if (n >= min)
        return (unsigned)n;
invalid:
    fprintf(stderr, "invalid %s\n", name);
    exit(2);
}
int main(int argc, char **argv) {
    const char *runtime = "/tmp/wan-watchdog", *adapter = NULL, *legacy = NULL,
               *ownership = "/jffs/wan-watchdog";
    int active = 0, pinned = 0;
    unsigned duration = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--active"))
            active = 1;
        else if (!strcmp(argv[i], "--shadow"))
            active = 0;
        else if (!strcmp(argv[i], "--run-dir") && i + 1 < argc)
            runtime = argv[++i];
        else if (!strcmp(argv[i], "--adapter") && i + 1 < argc)
            adapter = argv[++i];
        else if (!strcmp(argv[i], "--legacy") && i + 1 < argc)
            legacy = argv[++i];
        else if (!strcmp(argv[i], "--ownership") && i + 1 < argc)
            ownership = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc)
            duration = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) {
            const char *m = argv[++i];
            if (strcmp(m, "pinned") && strcmp(m, "whole_lan"))
                return 2;
            pinned = !strcmp(m, "pinned");
        } else if (!strcmp(argv[i], "--system-info")) {
            printf("clk_tck=%ld\n", sysconf(_SC_CLK_TCK));
            return 0;
        } else {
            fprintf(stderr, "usage: wan-watchdog-native --adapter PATH --legacy PATH "
                            "[--shadow|--active] [--run-dir DIR] [--ownership DIR] [--mode "
                            "whole_lan|pinned] [--duration SECONDS]\n");
            return 2;
        }
    }
    if (!adapter || !legacy || !safe_path(adapter) || !safe_path(legacy) || !safe_path(runtime) ||
        !safe_path(ownership))
        return 2;
    (void)env_number("FAILOVER_PRIORITY", 88, 3, 99);
    unsigned tables[3] = {env_number("FAILOVER_TABLE", 20188, 1000, 2147483647),
                          env_number("PROBE_TABLE0", 20186, 1000, 2147483647),
                          env_number("PROBE_TABLE1", 20187, 1000, 2147483647)};
    if (tables[0] == tables[1] || tables[0] == tables[2] || tables[1] == tables[2]) {
        fprintf(stderr, "duplicate watchdog table IDs\n");
        return 2;
    }
    if (!active && !strcmp(runtime, "/tmp/wan-watchdog")) {
        fprintf(stderr, "shadow needs a private runtime directory\n");
        return 2;
    }
    umask(077);
    struct stat st;
    if (mkdir(runtime, 0700) && errno != EEXIST)
        fatal("runtime");
    if (lstat(runtime, &st) || !S_ISDIR(st.st_mode) || st.st_uid != geteuid())
        fatal("runtime ownership");
    char path[256];
    snprintf(path, sizeof path, "%s/lock", runtime);
    int lock = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB))
        fatal("watchdog already owned");
    setenv("STATE_DIR", runtime, 1);
    setenv("OWNERSHIP_DIR", ownership, 1);
    setenv("WW_LEGACY", legacy, 1);
    setenv("WW_SHADOW", active ? "0" : "1", 1);
    setenv("FAILOVER_MODE", pinned ? "pinned" : "whole_lan", 1);
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGPIPE, SIG_IGN);
    monitor_log(LOG_INFO, "native monitor started; mode=%s shadow=%d",
                pinned ? "pinned" : "whole_lan", !active);
    snprintf(path, sizeof path, "%s/wan-watchdog.pid", runtime);
    FILE *pf = fopen(path, "w");
    if (!pf)
        fatal("pid");
    fprintf(pf, "%ld\n", (long)getpid());
    if (fclose(pf))
        fatal("pid close");
    wh_state state;
    memset(&state, 0, sizeof state);
    state.pinned = (unsigned)pinned;
    state.desired_failed = state.applied_failed = -1;
    if (wh_boot(state.boot) || wh_random(state.instance))
        fatal("publisher identity");
    uint64_t started = wh_now(), next_dispatch = started, next_refresh = started,
             next_status = started, deadline = 0, dispatch = 0, jitter = 0, cycle = 0,
             last_facts = 0;
    ww_core core;
    ww_init(&core, started);
    cache(runtime, &core, state.boot, 1);
    wh_server server;
    char socketpath[256];
    snprintf(socketpath, sizeof socketpath, "%s/health.sock", runtime);
    if (wh_server_open(&server, socketpath))
        perror("health socket unavailable; monitoring continues");
    uint64_t next_listener_retry = started + 5 * WH_NSEC;
    int timer = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
    if (timer < 0)
        fatal("timerfd");
    int netlink = route_events(); /* Periodic inspection remains the fallback. */
    ww_probe probes[2][2];
    memset(probes, 0, sizeof probes);
    for (int u = 0; u < 2; u++)
        for (int t = 0; t < 2; t++)
            probes[u][t].fd = -1;
    fact facts[2];
    memset(facts, 0, sizeof facts);
    for (int u = 0; u < 2; u++)
        facts[u].enable = -1;
    ww_flags flags[2];
    memset(flags, 0, sizeof flags);
    int initialized = 0, have_cycle = 0, need_apply = 0, restore = 0;
    ww_worker job;
    ww_worker_init(&job);
    view observation;
    context ctx;
    memset(&ctx, 0, sizeof ctx);
    snprintf(ctx.adapter, sizeof ctx.adapter, "%s", adapter);
    snprintf(ctx.runtime, sizeof ctx.runtime, "%s", runtime);
    ctx.pinned = pinned;
    unsigned refresh_ok = 0, refresh_failed = 0;
    int cache_mask = -1, failed_intent = -1;
    int last_pin[2] = {-1, -1}, last_active[2] = {-1, -1};
    uint64_t cycle_config[2] = {0};
    uint64_t next_instance_token = 0;
    memcpy(&next_instance_token, state.instance, sizeof next_instance_token);
    publish(&server, &state, &core, wh_now());
    uint64_t previous_mono = wh_now(), previous_boot = boot_time();
    while (!stopping && (!duration || wh_now() - started < (uint64_t)duration * WH_NSEC)) {
        uint64_t now = wh_now();
        uint64_t boot = boot_time();
        if (ww_clock_gap(now, boot, previous_mono, previous_boot)) {
            fprintf(stderr, "clock discontinuity; restarting measurement windows\n");
            for (int u = 0; u < 2; u++) {
                ww_reset(&core, u, now);
                memset(&flags[u], 0, sizeof flags[u]);
                flags[u].reason = WH_WARMING;
            }
            ww_worker_cancel(&job);
            have_cycle = 0;
            need_apply = 0;
            last_facts = 0;
            next_dispatch = next_refresh = now;
            publish(&server, &state, &core, now);
        }
        previous_mono = now;
        previous_boot = boot;
        if (server.listener < 0 && now >= next_listener_retry) {
            if (wh_server_open(&server, socketpath))
                perror("health socket retry");
            else
                publish(&server, &state, &core, now);
            next_listener_retry = now + 5 * WH_NSEC;
        }
        if (job.pid) {
            int rc = ww_worker_poll(&job, &observation, (double)now / 1e9, 5.0);
            if (rc) {
                if (rc < 0 || observation.magic != 0x57574e31 ||
                    now - observation.started > 5 * WH_NSEC) {
                    refresh_failed++;
                    state.routing_error = 1;
                } else {
                    unsigned previous_error = state.routing_error;
                    refresh_ok++;
                    last_facts = observation.started;
                    if (observation.operation)
                        state.routing_error = (unsigned)observation.action_error;
                    if (!initialized && active && observation.action_error) {
                        fprintf(stderr, "initial routing setup failed; preserving ledger\n");
                        break;
                    }
                    int first = !initialized;
                    initialized = 1;
                    failed_intent = observation.failed;
                    /* The persistent ledger records intent before mutation;
                     * it is not proof that a requested overlay was applied. */
                    if (observation.operation == 1 && observation.failed >= 0)
                        state.routing_error = 1;
                    if (observation.acted && !observation.action_error) {
                        int changed =
                            observation.generation != state.applied_generation ||
                            (!pinned && state.applied_failed !=
                                            (observation.restore ? -1 : observation.wanted));
                        if (!pinned)
                            state.applied_failed = observation.restore ? -1 : observation.wanted;
                        if (observation.generation >= state.applied_generation)
                            state.applied_generation = observation.generation;
                        if (changed)
                            monitor_log(LOG_INFO,
                                        "routing applied; generation=%llu failed=%d mode=%s",
                                        (unsigned long long)state.applied_generation,
                                        state.applied_failed, pinned ? "pinned" : "whole_lan");
                    }
                    if (previous_error != state.routing_error)
                        monitor_log(state.routing_error ? LOG_ERR : LOG_INFO,
                                    "routing worker error=%u", state.routing_error);
                    for (int u = 0; u < 2; u++) {
                        fact *f = &observation.wan[u];
                        int changed = f->enable != facts[u].enable || f->ip != facts[u].ip ||
                                      f->gateway != facts[u].gateway ||
                                      memcmp(f->ifname, facts[u].ifname, 16) ||
                                      memcmp(f->target, facts[u].target, 8);
                        if (changed) {
                            monitor_log(
                                LOG_INFO,
                                "WAN%d configuration changed; restarting measurement window", u);
                            ww_reset(&core, u, now);
                            memset(&flags[u], 0, sizeof flags[u]);
                            flags[u].reason = WH_WARMING;
                            for (int t = 0; t < 2; t++) {
                                if (probes[u][t].fd >= 0)
                                    close(probes[u][t].fd);
                                probes[u][t].fd = -1;
                                probes[u][t].result = -1;
                            }
                        }
                        facts[u] = *f;
                        if (f->active)
                            core.wan[u].excluded = 1;
                        wh_wan *h = &core.wan[u].health;
                        memcpy(h->ifname, f->ifname, 16);
                        h->ip = f->ip;
                        h->gateway = f->gateway;
                        memcpy(h->target, f->target, 8);
                    }
                    if (first)
                        next_dispatch = now;
                }
                publish(&server, &state, &core, now);
            }
        }
        if (have_cycle && now >= deadline) {
            wh_wan previous_health[2] = {core.wan[0].health, core.wan[1].health};
            for (int u = 0; u < 2; u++) {
                if (cycle_config[u] != core.wan[u].health.config)
                    continue;
                int a = probes[u][0].result, b = probes[u][1].result;
                if (!facts[u].ip && facts[u].enable == 1)
                    a = b = 0;
                if (!last_facts || now - last_facts > 5 * WH_NSEC)
                    a = b = -1;
                ww_sample_cycle(&core, u, dispatch, facts[u].enable, a, b, facts[u].active,
                                &flags[u]);
                /* Publication times describe completion, not future dispatch. */
                core.wan[u].health.measured_ns = now;
                if (a >= 0 && b >= 0 && facts[u].enable == 1)
                    core.wan[u].health.valid_ns = now;
            }
            if (active) {
                int markers[2] = {facts[0].active, facts[1].active};
                ww_admission_gate(&core, flags, markers, state.applied_failed,
                                  (int)state.routing_error, !pinned);
            }
            for (int u = 0; u < 2; u++) {
                wh_wan *h = &core.wan[u].health;
                if (h->reason != previous_health[u].reason ||
                    h->eligible != previous_health[u].eligible ||
                    h->excluded != previous_health[u].excluded)
                    monitor_log(LOG_INFO,
                                "WAN%d %s bad=%u acceptable=%u eligible=%u excluded=%u recovery=%u "
                                "loss=%d%%/%d%%",
                                u, wh_reason_name(h->reason), h->bad, h->acceptable, h->eligible,
                                h->excluded, h->recovery, flags[u].loss[0], flags[u].loss[1]);
            }
            have_cycle = 0;
            state.progress++;
            int wanted = ww_whole_intent(flags, failed_intent, &restore);
            int intent_changed = !pinned && wanted != state.desired_failed;
            if (!pinned)
                state.desired_failed = wanted;
            ctx.wanted = wanted;
            ctx.restore = restore;
            ctx.sampled = dispatch;
            for (int u = 0; u < 2; u++) {
                ctx.mask[u] = ww_pin_intent(flags, u, facts[u].active, &ctx.p[u], &ctx.a[u]);
                if (pinned && ctx.mask[u]) {
                    if (last_pin[u] != ctx.p[u] || last_active[u] != ctx.a[u])
                        intent_changed = 1;
                    last_pin[u] = ctx.p[u];
                    last_active[u] = ctx.a[u];
                }
            }
            if (intent_changed)
                state.desired_generation++;
            need_apply = active;
            int mask = core.wan[0].excluded | (core.wan[1].excluded << 1);
            if (mask != cache_mask) {
                cache(runtime, &core, state.boot, 0);
                cache_mask = mask;
            }
            publish(&server, &state, &core, now);
        }
        if (initialized && !have_cycle && now >= next_dispatch) {
            if (now - next_dispatch > jitter)
                jitter = now - next_dispatch;
            dispatch = next_dispatch;
            deadline = now + WH_NSEC;
            cycle++;
            while (dispatch + 2 * WH_NSEC <= now)
                dispatch += 2 * WH_NSEC;
            next_dispatch = dispatch + 2 * WH_NSEC;
            for (int u = 0; u < 2; u++)
                for (int t = 0; t < 2; t++) {
                    cycle_config[u] = core.wan[u].health.config;
                    ww_probe *p = &probes[u][t];
                    p->result = -1;
                    if (facts[u].enable != 1 || !facts[u].ip || facts[u].route[t] != 1 ||
                        now - last_facts > 5 * WH_NSEC)
                        continue;
                    if (p->fd < 0 && ww_probe_open(p, facts[u].ip, facts[u].ifname))
                        continue;
                    ww_probe_send(p, facts[u].target[t], next_instance_token + cycle,
                                  (uint16_t)cycle);
                }
            have_cycle = 1;
        }
        if (!job.pid && now >= next_refresh) {
            ctx.operation = !initialized && active ? 1 : need_apply ? 2 : 0;
            ctx.generation = state.desired_generation;
            memcpy(ctx.flags, flags, sizeof flags);
            ctx.nclose = 0;
            memcpy(ctx.expected, facts, sizeof facts);
            ctx.close_fds[ctx.nclose++] = server.listener;
            ctx.close_fds[ctx.nclose++] = timer;
            ctx.close_fds[ctx.nclose++] = netlink;
            for (int i = 0; i < WH_SUBSCRIBERS; i++)
                ctx.close_fds[ctx.nclose++] = server.peer[i].fd;
            for (int u = 0; u < 2; u++)
                for (int t = 0; t < 2; t++)
                    ctx.close_fds[ctx.nclose++] = probes[u][t].fd;
            if (ww_worker_start(&job, &observation, sizeof observation, collect, &ctx, -1,
                                (double)now / 1e9)) {
                refresh_failed++;
                state.routing_error = 1;
            }
            need_apply = 0;
            next_refresh = now + 2 * WH_NSEC;
        }
        if (now >= next_status) {
            status_file(runtime, &state, &server, !active, refresh_ok, refresh_failed, jitter);
            next_status = now + WH_NSEC;
        }
        struct pollfd p[WH_SERVER_FDS + 6];
        wh_server_fds(&server, p);
        p[WH_SERVER_FDS] = (struct pollfd){timer, POLLIN, 0};
        p[WH_SERVER_FDS + 1] = (struct pollfd){netlink, POLLIN, 0};
        for (int u = 0; u < 2; u++)
            for (int t = 0; t < 2; t++)
                p[WH_SERVER_FDS + 2 + u * 2 + t] = (struct pollfd){probes[u][t].fd, POLLIN, 0};
        uint64_t wake = job.pid ? now + 20000000 : next_status;
        if (!job.pid && next_refresh < wake)
            wake = next_refresh;
        if (initialized && !have_cycle && next_dispatch < wake)
            wake = next_dispatch;
        if (next_status < wake)
            wake = next_status;
        if (have_cycle && deadline < wake)
            wake = deadline;
        if (wake <= now)
            wake = now + 1000000;
        struct itimerspec its = {0};
        its.it_value.tv_sec = (time_t)(wake / WH_NSEC);
        its.it_value.tv_nsec = (long)(wake % WH_NSEC);
        if (timerfd_settime(timer, TFD_TIMER_ABSTIME, &its, NULL))
            fatal("timerfd_settime");
        int rc = poll(p, WH_SERVER_FDS + 6, -1);
        if (rc < 0 && errno != EINTR)
            fatal("poll");
        now = wh_now();
        wh_server_step(&server, p, now);
        if (p[WH_SERVER_FDS].revents & POLLIN) {
            uint64_t ticks;
            (void)read(timer, &ticks, sizeof ticks);
        }
        if (netlink >= 0 && p[WH_SERVER_FDS + 1].revents & POLLIN) {
            char b[8192];
            for (int i = 0; i < 4; i++)
                if (recv(netlink, b, sizeof b, MSG_DONTWAIT) < 0)
                    break;
            if (next_refresh > now + 200000000)
                next_refresh = now + 200000000;
        }
        /* Always drain raw sockets: they also receive unrelated ICMP between
         * cycles. Accepting results remains restricted to the active deadline. */
        for (int u = 0; u < 2; u++)
            for (int t = 0; t < 2; t++)
                if (p[WH_SERVER_FDS + 2 + u * 2 + t].revents & POLLIN)
                    ww_probe_receive(&probes[u][t], have_cycle && now <= deadline);
    }
    ww_worker_cancel(&job);
    for (int i = 0; job.pid && i < 50; i++) {
        ww_worker_poll(&job, &observation, (double)wh_now() / 1e9, 0);
        usleep(10000);
    }
    status_file(runtime, &state, &server, !active, refresh_ok, refresh_failed, jitter);
    wh_server_close(&server);
    close(timer);
    if (netlink >= 0)
        close(netlink);
    for (int u = 0; u < 2; u++)
        for (int t = 0; t < 2; t++)
            if (probes[u][t].fd >= 0)
                close(probes[u][t].fd);
    snprintf(path, sizeof path, "%s/wan-watchdog.pid", runtime);
    unlink(path);
    close(lock);
    monitor_log(LOG_INFO, "native monitor stopped; forwarding state preserved");
    if (log_fd >= 0)
        close(log_fd);
    return initialized ? 0 : 1;
}
