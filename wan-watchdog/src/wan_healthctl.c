#define _GNU_SOURCE
#include "wh_socket.h"
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    const char *path = "/tmp/wan-watchdog/health.sock";
    unsigned count = 1, timeout = 10;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--socket") && i + 1 < argc)
            path = argv[++i];
        else if (!strcmp(argv[i], "--count") && i + 1 < argc)
            count = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--timeout") && i + 1 < argc)
            timeout = (unsigned)strtoul(argv[++i], NULL, 10);
        else {
            fprintf(
                stderr,
                "usage: wan-healthctl [--socket PATH] [--count N (0=watch)] [--timeout SECONDS]\n");
            return 2;
        }
    }
    wh_client c;
    wh_client_init(&c, path);
    uint64_t start = wh_now();
    unsigned got = 0;
    while (!timeout || wh_now() - start < (uint64_t)timeout * WH_NSEC) {
        uint64_t now = wh_now();
        int rc = wh_client_step(&c, now);
        if (rc > 0) {
            const wh_state *s = &c.state;
            char boot[33], instance[33];
            for (int i = 0; i < 16; i++) {
                snprintf(boot + 2 * i, 3, "%02x", s->boot[i]);
                snprintf(instance + 2 * i, 3, "%02x", s->instance[i]);
            }
            printf("{\"boot\":\"%s\",\"instance\":\"%s\",\"seq\":%llu,\"progress\":%llu,"
                   "\"published_ns\":%llu,\"fresh\":%s,\"routing_error\":%u,\"desired\":%d,"
                   "\"applied\":%d,\"wan\":[",
                   boot, instance, (unsigned long long)s->seq, (unsigned long long)s->progress,
                   (unsigned long long)s->published_ns,
                   wh_client_fresh(&c, wh_now()) ? "true" : "false", s->routing_error,
                   s->desired_failed, s->applied_failed);
            for (int u = 0; u < 2; u++) {
                const wh_wan *w = &s->wan[u];
                char ip[INET_ADDRSTRLEN];
                inet_ntop(AF_INET, &w->ip, ip, sizeof ip);
                printf("%s{\"unit\":%u,\"reason\":\"%s\",\"bad\":%u,\"acceptable\":%u,\"eligible\":"
                       "%u,\"excluded\":%u,\"recovery\":%u,\"measured_ns\":%llu,\"config\":%llu,"
                       "\"ip\":\"%s\"}",
                       u ? "," : "", w->unit, wh_reason_name(w->reason), w->bad, w->acceptable,
                       w->eligible, w->excluded, w->recovery, (unsigned long long)w->measured_ns,
                       (unsigned long long)w->config, ip);
            }
            puts("]}");
            fflush(stdout);
            got++;
            if (count && got >= count) {
                wh_client_close(&c, wh_now());
                return 0;
            }
        }
        struct pollfd p = {c.fd, POLLIN, 0};
        poll(&p, 1, 100);
    }
    wh_client_close(&c, wh_now());
    return got ? 0 : 1;
}
