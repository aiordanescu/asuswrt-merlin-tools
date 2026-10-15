#include "wh_protocol.h"
#include <string.h>

/* Header 112 bytes, followed by two 136-byte records. */
#define HEADER 112u
#define RECORD 136u
#define WIRE (HEADER + 2u * RECORD)
static void put32(uint8_t **p, uint32_t v) {
    for (int i = 3; i >= 0; i--)
        *(*p)++ = (uint8_t)(v >> (i * 8));
}
static uint32_t get32(const uint8_t **p) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++)
        v = (v << 8) | *(*p)++;
    return v;
}
static void put64(uint8_t **p, uint64_t v) {
    put32(p, (uint32_t)(v >> 32));
    put32(p, (uint32_t)v);
}
static uint64_t get64(const uint8_t **p) {
    uint64_t v = get32(p);
    return (v << 32) | get32(p);
}
size_t wh_encode(void *buffer, size_t capacity, const wh_state *s) {
    if (capacity < WIRE)
        return 0;
    uint8_t *p = buffer;
    put32(&p, 0x57484c31);
    put32(&p, WH_VERSION);
    put32(&p, WIRE);
    put32(&p, HEADER);
    put32(&p, RECORD);
    put32(&p, 2);
    memcpy(p, s->boot, 16);
    p += 16;
    memcpy(p, s->instance, 16);
    p += 16;
    put64(&p, s->seq);
    put64(&p, s->published_ns);
    put64(&p, s->progress);
    put64(&p, s->desired_generation);
    put64(&p, s->applied_generation);
    put32(&p, s->pinned);
    put32(&p, s->routing_error);
    put32(&p, (uint32_t)s->desired_failed);
    put32(&p, (uint32_t)s->applied_failed);
    for (int u = 0; u < 2; u++) {
        const wh_wan *w = &s->wan[u];
        put32(&p, w->unit);
        put32(&p, w->reason);
        put32(&p, w->bad);
        put32(&p, w->acceptable);
        put32(&p, w->recovered);
        put32(&p, w->eligible);
        put32(&p, w->excluded);
        put32(&p, w->recovery);
        put64(&p, w->config);
        put64(&p, w->measured_ns);
        put64(&p, w->valid_ns);
        put64(&p, w->recovery_since_ns);
        put64(&p, w->transition);
        for (int t = 0; t < 2; t++) {
            put32(&p, w->samples[t]);
            put32(&p, w->lost[t]);
            put32(&p, w->unknown[t]);
            put32(&p, (uint32_t)w->latest[t]);
        }
        memcpy(p, w->ifname, 16);
        p += 16;
        memcpy(p, &w->ip, 4);
        p += 4;
        memcpy(p, &w->gateway, 4);
        p += 4;
        memcpy(p, w->target, 8);
        p += 8;
    }
    return (size_t)(p - (uint8_t *)buffer) == WIRE ? WIRE : 0;
}
int wh_decode(wh_state *out, const void *buffer, size_t length) {
    if (length < WIRE || length > WH_MAX_FRAME)
        return -1;
    const uint8_t *p = buffer;
    if (get32(&p) != 0x57484c31 || get32(&p) != WH_VERSION || get32(&p) != length ||
        get32(&p) != HEADER || get32(&p) != RECORD || get32(&p) != 2 || length != WIRE)
        return -1;
    wh_state s;
    memset(&s, 0, sizeof s);
    memcpy(s.boot, p, 16);
    p += 16;
    memcpy(s.instance, p, 16);
    p += 16;
    s.seq = get64(&p);
    s.published_ns = get64(&p);
    s.progress = get64(&p);
    s.desired_generation = get64(&p);
    s.applied_generation = get64(&p);
    s.pinned = get32(&p);
    s.routing_error = get32(&p);
    s.desired_failed = (int32_t)get32(&p);
    s.applied_failed = (int32_t)get32(&p);
    if (s.pinned > 1 || s.routing_error > 1 || s.desired_failed < -1 || s.desired_failed > 1 ||
        s.applied_failed < -1 || s.applied_failed > 1 ||
        s.applied_generation > s.desired_generation)
        return -1;
    for (int u = 0; u < 2; u++) {
        wh_wan *w = &s.wan[u];
        w->unit = get32(&p);
        w->reason = get32(&p);
        w->bad = get32(&p);
        w->acceptable = get32(&p);
        w->recovered = get32(&p);
        w->eligible = get32(&p);
        w->excluded = get32(&p);
        w->recovery = get32(&p);
        w->config = get64(&p);
        w->measured_ns = get64(&p);
        w->valid_ns = get64(&p);
        w->recovery_since_ns = get64(&p);
        w->transition = get64(&p);
        for (int t = 0; t < 2; t++) {
            w->samples[t] = get32(&p);
            w->lost[t] = get32(&p);
            w->unknown[t] = get32(&p);
            w->latest[t] = (int32_t)get32(&p);
        }
        memcpy(w->ifname, p, 16);
        p += 16;
        memcpy(&w->ip, p, 4);
        p += 4;
        memcpy(&w->gateway, p, 4);
        p += 4;
        memcpy(w->target, p, 8);
        p += 8;
        if (w->unit != (unsigned)u || w->reason > WH_UNREACHABLE || w->bad > 1 ||
            w->acceptable > 1 || w->recovered > 1 || w->eligible > 1 || w->excluded > 1 ||
            w->recovery > WH_COMPLETE || !memchr(w->ifname, 0, 16) ||
            (w->eligible && (w->bad || !w->acceptable || w->excluded)))
            return -1;
        for (int t = 0; t < 2; t++)
            if (w->latest[t] < -1 || w->latest[t] > 1 || w->samples[t] > 1024 ||
                w->lost[t] > w->samples[t] || w->unknown[t] > w->samples[t] - w->lost[t])
                return -1;
    }
    *out = s;
    return 0;
}
int wh_validate(const wh_state *s, const uint8_t boot[16], uint64_t now) {
    static const uint8_t zero[16] = {0};
    if (memcmp(s->boot, boot, 16) || !memcmp(s->instance, zero, 16) || !s->seq ||
        s->published_ns > now || now - s->published_ns > WH_MAX_AGE_NS)
        return -1;
    for (int u = 0; u < 2; u++) {
        const wh_wan *w = &s->wan[u];
        if (w->measured_ns > s->published_ns || w->valid_ns > w->measured_ns ||
            w->recovery_since_ns > s->published_ns)
            return -1;
        if (w->eligible && (!w->measured_ns || now - w->measured_ns > WH_MAX_AGE_NS ||
                            !w->valid_ns || now - w->valid_ns > WH_MAX_AGE_NS))
            return -1;
    }
    return 0;
}
int wh_uuid(const char *text, uint8_t out[16]) {
    unsigned n = 0, high = 0;
    uint8_t result[16] = {0};
    for (const char *p = text; *p && *p != '\n'; p++) {
        if (*p == '-')
            continue;
        unsigned v = *p >= '0' && *p <= '9'   ? (unsigned)(*p - '0')
                     : *p >= 'a' && *p <= 'f' ? (unsigned)(*p - 'a' + 10)
                     : *p >= 'A' && *p <= 'F' ? (unsigned)(*p - 'A' + 10)
                                              : 16;
        if (v > 15 || n >= 32)
            return -1;
        if (!(n & 1))
            high = v;
        else
            result[n / 2] = (uint8_t)((high << 4) | v);
        n++;
    }
    if (n != 32)
        return -1;
    memcpy(out, result, 16);
    return 0;
}
const char *wh_reason_name(unsigned reason) {
    static const char *names[] = {"warming", "usable",   "disabled",
                                  "unknown", "degraded", "unreachable"};
    return reason <= WH_UNREACHABLE ? names[reason] : "invalid";
}
