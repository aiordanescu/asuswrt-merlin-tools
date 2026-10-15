#ifndef WH_PROTOCOL_H
#define WH_PROTOCOL_H
#include <stddef.h>
#include <stdint.h>

#define WH_VERSION 1
#define WH_MAX_FRAME 1024
#define WH_MAX_AGE_NS UINT64_C(15000000000)
#define WH_NSEC UINT64_C(1000000000)
enum wh_reason { WH_WARMING, WH_USABLE, WH_DISABLED, WH_UNKNOWN, WH_DEGRADED, WH_UNREACHABLE };
enum wh_recovery { WH_NOT_REQUIRED, WH_HELD, WH_COMPLETE };
typedef struct {
    uint32_t unit, reason, bad, acceptable, recovered, eligible, excluded, recovery;
    uint64_t config, measured_ns, valid_ns, recovery_since_ns, transition;
    uint32_t samples[2], lost[2], unknown[2];
    int32_t latest[2];
    char ifname[16];
    uint32_t ip, gateway, target[2]; /* IPv4 addresses in network byte order. */
} wh_wan;
typedef struct {
    uint8_t boot[16], instance[16];
    uint64_t seq, published_ns, progress;
    uint64_t desired_generation, applied_generation;
    uint32_t pinned, routing_error;
    int32_t desired_failed, applied_failed; /* -1 == no whole-LAN override */
    wh_wan wan[2];
} wh_state;

/* Explicit big-endian wire encoding. Never send sizeof(wh_state). */
size_t wh_encode(void *buffer, size_t capacity, const wh_state *state);
int wh_decode(wh_state *state, const void *buffer, size_t length);
int wh_validate(const wh_state *state, const uint8_t boot[16], uint64_t now);
int wh_uuid(const char *text, uint8_t out[16]);
const char *wh_reason_name(unsigned reason);
#endif
