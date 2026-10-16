/* Test-only publisher. Never included in the production daemon. */
#define _GNU_SOURCE
#include "wh_socket.h"
#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static volatile sig_atomic_t stop;
static void stopping(int s) {
    (void)s;
    stop = 1;
}
int main(int argc, char **argv) {
    if (argc != 4 || strcmp(argv[1], "--permit-test-fixture") || strncmp(argv[2], "/tmp/", 5) ||
        !strcmp(argv[2], "/tmp/wan-watchdog/health.sock"))
        return 2;
    signal(SIGTERM, stopping);
    signal(SIGINT, stopping);
    signal(SIGPIPE, SIG_IGN);
    wh_server server;
    if (wh_server_open(&server, argv[2]))
        return 1;
    wh_state s;
    memset(&s, 0, sizeof s);
    if (wh_boot(s.boot) || wh_random(s.instance))
        return 1;
    s.desired_failed = s.applied_failed = -1;
    uint64_t next = 0;
    while (!stop) {
        uint64_t now = wh_now();
        if (now >= next) {
            FILE *f = fopen(argv[3], "r");
            int seen = 0;
            if (f) {
                unsigned u, bad, eligible, recovered;
                char ip[32], iface[16];
                while (fscanf(f, "%u %u %u %u %31s %15s", &u, &bad, &eligible, &recovered, ip,
                              iface) == 6) {
                    if (u > 1 || bad > 1 || eligible > 1 || recovered > 1 || seen & (1 << u))
                        break;
                    wh_wan *w = &s.wan[u];
                    memset(w, 0, sizeof *w);
                    w->unit = u;
                    w->reason = bad ? WH_DEGRADED : WH_USABLE;
                    w->bad = bad;
                    w->eligible = eligible;
                    w->excluded = !eligible;
                    w->acceptable = !bad;
                    w->recovered = recovered;
                    w->recovery = !eligible ? WH_HELD : recovered ? WH_COMPLETE : WH_NOT_REQUIRED;
                    w->measured_ns = w->valid_ns = now;
                    strcpy(w->ifname, iface);
                    for (int t = 0; t < 2; t++) {
                        w->samples[t] = 15;
                        w->lost[t] = bad ? 7 : 0;
                        w->latest[t] = bad ? 0 : 1;
                    }
                    if (inet_pton(AF_INET, ip, &w->ip) != 1)
                        break;
                    seen |= 1 << u;
                }
                fclose(f);
            }
            if (seen == 3) {
                s.seq++;
                s.progress++;
                s.published_ns = now;
                if (wh_server_publish(&server, &s, now))
                    return 1;
            }
            next = now + 200000000;
        }
        struct pollfd p[WH_SERVER_FDS];
        wh_server_fds(&server, p);
        poll(p, WH_SERVER_FDS, 50);
        wh_server_step(&server, p, wh_now());
    }
    wh_server_close(&server);
    return 0;
}
