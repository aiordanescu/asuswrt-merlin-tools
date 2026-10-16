#define _GNU_SOURCE
#include "wa_health_client.h"
#include "wh_socket.h"
#include <arpa/inet.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
static int checks;
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        checks++;                                                                                  \
        if (!(x)) {                                                                                \
            fprintf(stderr, "line %d: %s\n", __LINE__, #x);                                        \
            abort();                                                                               \
        }                                                                                          \
    } while (0)
static void server_tick(wh_server *s) {
    struct pollfd p[WH_SERVER_FDS];
    wh_server_fds(s, p);
    poll(p, WH_SERVER_FDS, 1);
    wh_server_step(s, p, wh_now());
}
static void state_init(wh_state *s) {
    memset(s, 0, sizeof *s);
    CHECK(!wh_boot(s->boot));
    CHECK(!wh_random(s->instance));
    s->seq = 1;
    s->published_ns = wh_now();
    s->desired_failed = s->applied_failed = -1;
    for (int u = 0; u < 2; u++) {
        wh_wan *w = &s->wan[u];
        w->unit = (unsigned)u;
        w->reason = WH_USABLE;
        w->eligible = w->acceptable = 1;
        w->measured_ns = w->valid_ns = s->published_ns;
        strcpy(w->ifname, "lo");
        inet_pton(AF_INET, "127.0.0.1", &w->ip);
    }
}
static void refresh(wh_server *s, wh_state *state) {
    state->seq++;
    state->published_ns = wh_now();
    for (int u = 0; u < 2; u++)
        state->wan[u].measured_ns = state->wan[u].valid_ns = state->published_ns;
    CHECK(!wh_server_publish(s, state, wh_now()));
}
int main(void) {
    char dir[100], path[108];
    snprintf(dir, sizeof dir, "/tmp/wh-test-%ld", (long)getpid());
    CHECK(!mkdir(dir, 0700));
    snprintf(path, sizeof path, "%s/s", dir);
    wh_server server;
    CHECK(!wh_server_open(&server, path));
    wh_state state;
    state_init(&state);
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    wh_client a, b;
    wh_client_init(&a, path);
    wh_client_init(&b, path);
    for (int i = 0; i < 20; i++) {
        wh_client_step(&a, wh_now());
        wh_client_step(&b, wh_now());
        server_tick(&server);
    }
    CHECK(a.connected == 2 && b.connected == 2);
    CHECK(a.state.seq == 1 && b.state.seq == 1);
    int slow = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    CHECK(slow >= 0);
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    strcpy(addr.sun_path, path);
    CHECK(!connect(slow, (void *)&addr, sizeof addr));
    server_tick(&server);
    for (int i = 0; i < 300; i++) {
        refresh(&server, &state);
        server_tick(&server);
        wh_client_step(&a, wh_now());
        wh_client_step(&b, wh_now());
        CHECK(a.state.seq == state.seq && b.state.seq == state.seq);
    }
    int pending = 0;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        pending += server.peer[i].pending_since > 0;
    CHECK(pending == 1);
    struct pollfd p[WH_SERVER_FDS];
    wh_server_fds(&server, p);
    poll(p, WH_SERVER_FDS, 0);
    wh_server_step(&server, p, wh_now() + WH_MAX_AGE_NS + WH_NSEC);
    CHECK(server.dropped == 1);
    close(slow);
    wh_server other;
    CHECK(wh_server_open(&other, path)); /* Preserve a live endpoint. */
    wh_server_close(&server);
    wh_client_step(&a, wh_now());
    CHECK(a.fd < 0);
    CHECK(!wh_server_open(&server, path));
    state_init(&state);
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    a.retry_ns = 0;
    for (int i = 0; i < 20; i++) {
        wh_client_step(&a, wh_now());
        server_tick(&server);
    }
    CHECK(a.connected == 2 && a.state.seq == 1); /* New instance, reset sequence. */
    wa_engine e;
    wa_engine_init(&e, WA_STICKY, 2, 1, 0, 15, 2, "", 1);
    for (int u = 0; u < 2; u++) {
        wa_wan_facts f = {.enable = 1, .state_t = 2, .route_ready = 1};
        strcpy(f.ip, "127.0.0.1");
        strcpy(f.gw, "127.0.0.1");
        strcpy(f.ifname, "lo");
        wa_health_set_native(&e, u, &f);
    }
    wa_health_feed feed;
    wa_health_feed_init(&feed, &e, WA_HEALTH_SOCKET, path, dir);
    CHECK(wa_health_eligibility(&e, 0) == WA_UNKNOWN);
    state.wan[0].reason = WH_DEGRADED;
    state.wan[0].bad = state.wan[0].excluded = 1;
    state.wan[0].eligible = state.wan[0].acceptable = 0;
    refresh(&server, &state);
    for (int i = 0; i < 20; i++) {
        wa_health_feed_step(&feed, &e, wh_now());
        server_tick(&server);
    }
    CHECK(wa_health_eligibility(&e, 0) == WA_UNUSABLE &&
          wa_health_eligibility(&e, 1) == WA_ELIGIBLE);
    wa_health old = e.health;
    old.snap.state[0] = WA_ELIGIBLE;
    old.excluded[0] = 0;
    wa_health_merge_native(&e, &old);
    CHECK(wa_health_eligibility(&e, 0) == WA_UNUSABLE);
    wh_client_close(&feed.client, wh_now());
    wa_health_feed_init(&feed, &e, WA_HEALTH_AUTO, path, dir);
    CHECK(e.health.excluded[0]); /* Same-boot exclusion survived a helper restart. */
    strcpy(e.health.native[0].ip, "127.0.0.3");
    state.wan[0].bad = state.wan[0].excluded = 0;
    state.wan[0].reason = WH_USABLE;
    state.wan[0].eligible = state.wan[0].acceptable = 1;
    refresh(&server, &state);
    for (int i = 0; i < 20; i++) {
        wa_health_feed_step(&feed, &e, wh_now());
        server_tick(&server);
    }
    CHECK(e.health.excluded[0] && wa_health_eligibility(&e, 0) == WA_UNKNOWN);
    wh_client_close(&feed.client, wh_now());
    wa_health_feed_init(&feed, &e, WA_HEALTH_AUTO, path, dir);
    CHECK(e.health.excluded[0]); /* Old-lease health cannot clear the checkpoint. */
    /* Identical-sequence replay inside a connection is rejected. */
    wh_client_step(&a, wh_now());
    CHECK(a.state.seq == state.seq);
    uint64_t rejected = a.rejected;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        if (server.peer[i].fd >= 0)
            server.peer[i].sent = 0;
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    wh_client_step(&a, wh_now());
    CHECK(a.rejected == rejected + 1 && !wh_client_fresh(&a, wh_now()));
    /* One WAN's stale evidence cannot be hidden by fresh publications. */
    state.wan[1].measured_ns = state.wan[1].valid_ns = 1;
    CHECK(wh_validate(&state, state.boot, wh_now()));
    /* Boot changes and unsupported frames never replace the last good view. */
    wh_state decoded;
    uint8_t packet[WH_MAX_FRAME];
    size_t length = wh_encode(packet, sizeof packet, &state);
    CHECK(length > 0);
    packet[7] = 99;
    CHECK(wh_decode(&decoded, packet, length));
    wh_client_close(&feed.client, wh_now());
    wh_client_close(&a, wh_now());
    wh_client_close(&b, wh_now());
    wh_server_close(&server);
    char cache[200];
    snprintf(cache, sizeof cache, "%s/health.cache", dir);
    unlink(cache);
    CHECK(!rmdir(dir));
    printf("socket/health merge: %d checks, 0 failures\n", checks);
    return 0;
}
