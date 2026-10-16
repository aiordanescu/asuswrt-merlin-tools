#define _GNU_SOURCE
#include "ww_probe.h"
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        checks++;                                                                                  \
        if (!(x)) {                                                                                \
            fprintf(stderr, "line %d: %s\n", __LINE__, #x);                                        \
            abort();                                                                               \
        }                                                                                          \
    } while (0)
int main(void) {
    int checks = 0;
    CHECK(ww_probe_error_result(ENETDOWN) == 0);
    CHECK(ww_probe_error_result(ENETUNREACH) == 0);
    CHECK(ww_probe_error_result(EHOSTUNREACH) == 0);
    CHECK(ww_probe_error_result(ENOBUFS) == -1);
    CHECK(ww_probe_error_result(EADDRNOTAVAIL) == -1);
    CHECK(ww_probe_error_result(EAGAIN) == -1);
    uint32_t loopback;
    CHECK(inet_pton(AF_INET, "127.0.0.1", &loopback) == 1);
    ww_probe p, other;
    CHECK(!ww_probe_open(&p, loopback, "lo"));
    CHECK(!ww_probe_open(&other, loopback, "lo"));
    ww_probe_send(&p, loopback, 1234, 1);
    CHECK(p.result == 0);
    usleep(10000);
    ww_probe_receive(&p, 0);
    CHECK(p.result == 0);
    struct pollfd fd = {p.fd, POLLIN, 0};
    CHECK(poll(&fd, 1, 0) == 0); /* Inactive cycles drain too. */
    ww_probe_send(&p, loopback, 1235, 2);
    usleep(10000);
    ww_probe_receive(&p, 1);
    CHECK(p.result == 1);
    p.result = 0;
    ww_probe_send(&other, loopback, 9999, 2);
    usleep(10000);
    ww_probe_receive(&p, 1);
    CHECK(p.result == 0);
    ww_probe_send(&other, loopback, 1235, 3);
    usleep(10000);
    ww_probe_receive(&p, 1);
    CHECK(p.result == 0);
    ww_probe_receive(&other, 0);
    close(other.fd);
    close(p.fd);
    printf("probe isolation/deadline draining: %d checks, 0 failures\n", checks);
    return 0;
}
