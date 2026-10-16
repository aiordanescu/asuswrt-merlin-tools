#define _GNU_SOURCE
#include "ww_probe.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/ip.h>
#include <linux/icmp.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
static uint16_t checksum(const void *data, size_t n) {
    const uint8_t *p = data;
    uint32_t sum = 0;
    while (n > 1) {
        sum += ((unsigned)p[0] << 8) | p[1];
        p += 2;
        n -= 2;
    }
    if (n)
        sum += (unsigned)*p << 8;
    while (sum >> 16)
        sum = (sum & 65535) + (sum >> 16);
    return htons((uint16_t)~sum);
}
int ww_probe_error_result(int error) {
    /* With an inspected route/source, a failed network transmission is a
     * lost attempt just as it is for ping. Local resource/permission/config
     * errors remain UNKNOWN; they must not manufacture WAN degradation. */
    return error == ENETDOWN || error == ENETUNREACH || error == EHOSTDOWN || error == EHOSTUNREACH
               ? 0
               : -1;
}
int ww_probe_open(ww_probe *p, uint32_t source, const char *ifname) {
    memset(p, 0, sizeof *p);
    p->fd = -1;
    p->result = -1;
    p->source = source;
    int fd = socket(AF_INET, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, IPPROTO_ICMP);
    if (fd < 0)
        return -1;
    struct sockaddr_in a = {.sin_family = AF_INET};
    a.sin_addr.s_addr = source;
    struct icmp_filter filter = {.data = ~(1U << ICMP_ECHOREPLY)};
    if (setsockopt(fd, IPPROTO_RAW, ICMP_FILTER, &filter, sizeof filter) ||
        bind(fd, (void *)&a, sizeof a) ||
        setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE, ifname, (socklen_t)(strlen(ifname) + 1))) {
        close(fd);
        return -1;
    }
    p->fd = fd;
    p->id = (uint16_t)(getpid() ^ (source >> 16) ^ source);
    return 0;
}
void ww_probe_send(ww_probe *p, uint32_t target, uint64_t token, uint16_t sequence) {
    uint8_t packet[24] = {0};
    struct icmphdr *h = (void *)packet;
    h->type = ICMP_ECHO;
    h->un.echo.id = htons(p->id);
    h->un.echo.sequence = htons(sequence);
    memcpy(packet + 8, "WWP1", 4);
    memcpy(packet + 12, &token, 8);
    memcpy(packet + 20, &target, 4);
    h->checksum = checksum(packet, sizeof packet);
    struct sockaddr_in a = {.sin_family = AF_INET};
    a.sin_addr.s_addr = target;
    p->target = target;
    p->token = token;
    p->sequence = sequence;
    p->result = -1;
    if (p->fd >= 0) {
        ssize_t sent = sendto(p->fd, packet, sizeof packet, MSG_DONTWAIT, (void *)&a, sizeof a);
        if (sent == sizeof packet)
            p->result = 0;
        else if (sent < 0)
            p->result = ww_probe_error_result(errno);
    }
}
void ww_probe_receive(ww_probe *p, int accept_reply) {
    for (int i = 0; i < 16; i++) {
        uint8_t b[2048];
        struct sockaddr_in from;
        socklen_t len = sizeof from;
        ssize_t n = recvfrom(p->fd, b, sizeof b, MSG_DONTWAIT, (void *)&from, &len);
        if (n < 0)
            return;
        if (n < 20 || from.sin_addr.s_addr != p->target)
            continue;
        size_t ihl = (size_t)(b[0] & 15) * 4;
        if ((b[0] >> 4) != 4 || ihl < 20 || ihl + 24 != (size_t)n ||
            checksum(b + ihl, (size_t)n - ihl))
            continue;
        const struct iphdr *ip = (void *)b;
        const struct icmphdr *h = (void *)(b + ihl);
        uint64_t token;
        memcpy(&token, b + ihl + 12, 8);
        if (accept_reply && ip->daddr == p->source && h->type == ICMP_ECHOREPLY && !h->code &&
            h->un.echo.id == htons(p->id) && h->un.echo.sequence == htons(p->sequence) &&
            !memcmp(b + ihl + 8, "WWP1", 4) && token == p->token &&
            !memcmp(b + ihl + 20, &p->target, 4))
            p->result = 1;
    }
}
