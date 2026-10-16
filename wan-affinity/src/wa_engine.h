/*
 * wan-affinity decision engine (pure C, no Linux/netlink dependency).
 *
 * This is the on-router policy core. It is deliberately free of platform
 * includes so the repository's host tests can compile and exercise it. The
 * netlink/conntrack/nvram adapters live in wan_affinity.c.
 *
 * Semantics mirror the Python reference model in ../wan_affinity/.
 */
#ifndef WA_ENGINE_H
#define WA_ENGINE_H

#include <stddef.h>
#include <stdint.h>

#define WA_WAN0_MARK   0x80000000u
#define WA_WAN1_MARK   0x90000000u
#define WA_WAN_MASK    0xf0000000u
#define WA_NONWAN_MASK 0x0fffffffu

/* Pass-through NF_REPEAT guard within the reserved WAN mark field. Using
 * an unrelated low bit would corrupt that bit for excluded/pinned traffic. */
#define WA_PASS_SENTINEL 0xa0000000u

#define WA_MAX_ASSOC 1024
#define WA_MAX_STATES 4096
#define WA_MAX_CIDR 8
#define WA_MAX_PINS 64
#define WA_DUMP_HEADER "#wan-affinity-conntrack v1 count="
#define WA_DUMP_END "--end--"
#define WA_ERR_LEN 48

typedef enum { WA_STICKY = 0, WA_AFFINITY = 1 } wa_mode;
typedef enum { WA_ELIGIBLE = 0, WA_UNUSABLE = 1, WA_UNKNOWN = 2 } wa_elig;
typedef enum { WA_ACT_PASS = 0, WA_ACT_MARK = 1 } wa_action;

typedef struct {
    int enable;         /* -1 unknown */
    int state_t;        /* 2 == connected, -1 unknown */
    int route_ready;    /* -1 unknown */
    char ip[16];
    char gw[16];
    char ifname[16];
} wa_wan_facts;

typedef struct {
    int valid;
    wa_elig state[2];
    int eligible[2];
    int recovered[2];
    unsigned long long seq;
    double published_mono;
    char boot_id[64];
} wa_snapshot;

typedef struct {
    wa_wan_facts native[2];
    wa_snapshot snap;
    int have_snap;
    double snap_mono;
    int enhanced;
    int native_policy;
    int excluded[2];
    unsigned long long last_seq;
    int have_seq;
} wa_health;

typedef struct {
    uint64_t ctid;
    uint32_t src, dst;
    uint32_t sport, dport;
    uint8_t proto;      /* 6 tcp, 17 udp */
    int8_t wan;         /* -1 unknown */
    uint8_t provisional;
    uint8_t dir_reply;
    double created, updated;
} wa_state;

typedef struct {
    uint32_t src;
    int wan;
    int preferred;      /* -1 for sticky */
    int adopted;
    int failovers;
    const char *reason;
    unsigned long long epoch;
    double created;
    double last_change;
    int have_candidate;
    double candidate_since;
    int have_confirmed;
    double confirmed_empty;
} wa_assoc;

typedef struct {
    uint32_t net, mask;
} wa_cidr;

typedef struct {
    wa_cidr local[WA_MAX_CIDR * 3];
    int n_local;
    wa_cidr vpn[WA_MAX_CIDR];
    int n_vpn;
    struct { uint32_t src, smask, dst, dmask; } pins[WA_MAX_PINS];
    int n_pins;
} wa_domain;

typedef struct {
    wa_mode mode;
    int weight[2];
    char affinity_seed[65];
    double idle_seconds;
    double max_reconcile_age;
    double provisional_warning_seconds;
    wa_cidr lan;
    wa_domain domain;
    wa_health health;
    wa_assoc assoc[WA_MAX_ASSOC];
    int n_assoc;
    wa_state state[WA_MAX_STATES];
    int n_state;
    uint32_t transitional[WA_MAX_ASSOC];
    int n_transitional;
    int have_reconcile;
    double last_reconcile_mono;
    int uncertain;
    char last_error[WA_ERR_LEN];
    unsigned long long epoch;
    size_t admissions;
    unsigned long long rng;
    uint64_t next_flow_token;
    int tuple_index[WA_MAX_STATES * 2];
    int tuple_index_valid;
} wa_engine;

typedef struct {
    wa_action action;
    int wan;
    uint32_t out_mark;
    const char *reason;
} wa_admission;

void wa_engine_init(wa_engine *e, wa_mode mode, int w0, int w1,
                    double idle_seconds, double max_reconcile_age,
                    double provisional_warning_seconds,
                    const char *affinity_seed, unsigned long long rng_seed);
void wa_domain_set_lan(wa_engine *e, uint32_t net, uint32_t mask);
void wa_domain_add_local(wa_engine *e, uint32_t net, uint32_t mask);
void wa_domain_add_vpn(wa_engine *e, uint32_t net, uint32_t mask);
void wa_domain_add_pin(wa_engine *e, uint32_t src, uint32_t smask, uint32_t dst, uint32_t dmask);

void wa_health_set_native(wa_engine *e, int unit, const wa_wan_facts *f);
int wa_health_apply_snapshot(wa_engine *e, const char *text, double now);
void wa_health_refresh(wa_engine *e, double now);
void wa_health_switch_native(wa_engine *e);
wa_elig wa_health_eligibility(wa_engine *e, int unit);

/* Lifecycle tracking (from conntrack events). Arrival order is not trusted;
 * only a validated dump may confirm emptiness, so DESTROY never expires. */
void wa_event_new(wa_engine *e, double now, const wa_state *st, int update);
void wa_event_destroy(wa_engine *e, double now, uint64_t ctid);

wa_admission wa_admission_decision(wa_engine *e, double now, uint32_t src, uint32_t dst,
                                   uint8_t proto, uint32_t sport, uint32_t dport,
                                   uint32_t mark, uint64_t ctid);

int wa_reconcile(wa_engine *e, double now, const char *framed_dump, int read_ok);
/* A validated U-then-C snapshot covers unconfirmed AND confirmed states.
 * Admissions at/after started are retained even if absent from that view. */
int wa_reconcile_view(wa_engine *e, double started, double applied,
                      const wa_state *states, int count, int read_ok);
void wa_reconstruct(wa_engine *e);
int wa_tick(wa_engine *e, double now);

uint64_t wa_ctid_hash(uint8_t proto, uint32_t src, uint32_t dst, uint32_t sport, uint32_t dport);
/* Default conntrack zone only. Tokens are exact-tuple identities, not hashes.
 * Delayed lifecycle events must not delete these tokens; validated dumps own
 * retirement. Reusing a tuple conservatively retains source liveness. */
uint64_t wa_flow_token(wa_engine *e, uint8_t proto, uint32_t src, uint32_t dst,
                       uint32_t sport, uint32_t dport);
int wa_parse_ipv4(const char *s, uint32_t *out);
int wa_parse_cidr(const char *s, uint32_t *net, uint32_t *mask);
void wa_ip_to_str(uint32_t ip, char *buf, size_t len);
int wa_wan_from_mark(uint32_t mark);
uint32_t wa_apply_wan_mark(uint32_t mark, int wan);
void wa_engine_status(const wa_engine *e, char *buf, size_t len);

#endif /* WA_ENGINE_H */
