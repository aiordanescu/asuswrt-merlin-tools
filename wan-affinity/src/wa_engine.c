/* wan-affinity decision engine. Pure C; see wa_engine.h. */

#include "wa_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WA_FNV_OFFSET 1469598103934665603ULL
#define WA_FNV_PRIME 1099511628211ULL
#define WA_SNAPSHOT_MAX_AGE 15.0

/* ------------------------------------------------------------------ */
/* small helpers                                                      */
/* ------------------------------------------------------------------ */

static uint64_t fnv_bytes(uint64_t h, const void *data, size_t len) {
    const unsigned char *p = (const unsigned char *)data;
    for (size_t i = 0; i < len; i++) {
        h ^= p[i];
        h *= WA_FNV_PRIME;
    }
    return h;
}

static uint64_t fnv_u32(uint64_t h, uint32_t v) {
    unsigned char b[4] = {(unsigned char)(v >> 24), (unsigned char)(v >> 16),
                          (unsigned char)(v >> 8), (unsigned char)v};
    return fnv_bytes(h, b, 4);
}

void wa_ip_to_str(uint32_t ip, char *buf, size_t len) {
    snprintf(buf, len, "%u.%u.%u.%u", (ip >> 24) & 0xff, (ip >> 16) & 0xff,
             (ip >> 8) & 0xff, ip & 0xff);
}

int wa_parse_ipv4(const char *s, uint32_t *out) {
    unsigned a, b, c, d;int end=0;
    if (sscanf(s, "%u.%u.%u.%u%n", &a, &b, &c, &d,&end) != 4 || s[end]) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255) return -1;
    *out = (a << 24) | (b << 16) | (c << 8) | d;
    return 0;
}

int wa_parse_cidr(const char *s, uint32_t *net, uint32_t *mask) {
    char buf[40];
    snprintf(buf, sizeof buf, "%s", s);
    int bits = 32;
    char *slash = strchr(buf, '/');
    if (slash) {
        *slash = 0;
        bits = atoi(slash + 1);
        if (bits < 0 || bits > 32) return -1;
    }
    uint32_t ip;
    if (wa_parse_ipv4(buf, &ip)) return -1;
    uint32_t m = (bits == 0) ? 0u : (bits == 32 ? 0xffffffffu : (0xffffffffu << (32 - bits)));
    *net = ip & m;
    *mask = m;
    return 0;
}

static int in_cidr(uint32_t ip, uint32_t net, uint32_t mask) {
    return (ip & mask) == (net & mask);
}

int wa_wan_from_mark(uint32_t m) {
    uint32_t t = m & WA_WAN_MASK;
    if (t == WA_WAN0_MARK) return 0;
    if (t == WA_WAN1_MARK) return 1;
    return -1;
}

uint32_t wa_apply_wan_mark(uint32_t m, int wan) {
    return (m & WA_NONWAN_MASK) | (wan == 0 ? WA_WAN0_MARK : WA_WAN1_MARK);
}

uint64_t wa_ctid_hash(uint8_t proto, uint32_t src, uint32_t dst, uint32_t sport, uint32_t dport) {
    uint64_t h = WA_FNV_OFFSET;
    h = fnv_bytes(h, &proto, 1);
    h = fnv_u32(h, src);
    h = fnv_u32(h, dst);
    h = fnv_u32(h, sport);
    h = fnv_u32(h, dport);
    return h ? h : 1u;
}

/* ------------------------------------------------------------------ */
/* construction / domain                                              */
/* ------------------------------------------------------------------ */

void wa_domain_set_lan(wa_engine *e, uint32_t net, uint32_t mask) {
    e->lan.net = net & mask;
    e->lan.mask = mask;
}

void wa_domain_add_local(wa_engine *e, uint32_t net, uint32_t mask) {
    if (e->domain.n_local < (int)(sizeof e->domain.local / sizeof e->domain.local[0])) {
        e->domain.local[e->domain.n_local].net = net & mask;
        e->domain.local[e->domain.n_local].mask = mask;
        e->domain.n_local++;
    }
}

void wa_domain_add_vpn(wa_engine *e, uint32_t net, uint32_t mask) {
    if (e->domain.n_vpn < WA_MAX_CIDR) {
        e->domain.vpn[e->domain.n_vpn].net = net & mask;
        e->domain.vpn[e->domain.n_vpn].mask = mask;
        e->domain.n_vpn++;
    }
}

void wa_domain_add_pin(wa_engine *e, uint32_t src, uint32_t smask, uint32_t dst, uint32_t dmask) {
    if (e->domain.n_pins < WA_MAX_PINS) {
        int i = e->domain.n_pins++;
        e->domain.pins[i].src = src & smask;
        e->domain.pins[i].smask = smask;
        e->domain.pins[i].dst = dst & dmask;
        e->domain.pins[i].dmask = dmask;
    }
}

void wa_engine_init(wa_engine *e, wa_mode mode, int w0, int w1,
                    double idle_seconds, double max_reconcile_age,
                    double provisional_warning_seconds,
                    const char *affinity_seed, unsigned long long rng_seed) {
    memset(e, 0, sizeof *e);
    e->mode = mode;
    e->weight[0] = w0 > 0 ? w0 : 1;
    e->weight[1] = w1 > 0 ? w1 : 1;
    e->idle_seconds = idle_seconds;
    e->max_reconcile_age = max_reconcile_age;
    e->provisional_warning_seconds = provisional_warning_seconds;
    snprintf(e->affinity_seed, sizeof e->affinity_seed, "%s", affinity_seed ? affinity_seed : "");
    e->rng = rng_seed ? rng_seed : 0x9e3779b97f4a7c15ULL;
    for (int u = 0; u < 2; u++) {
        e->health.firmware[u].enable = -1;
        e->health.firmware[u].state_t = -1;
        e->health.firmware[u].route_ready = -1;
    }
    const char *locals[] = {"192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
                            "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4"};
    for (size_t i = 0; i < sizeof locals / sizeof locals[0]; i++) {
        uint32_t n, m;
        if (wa_parse_cidr(locals[i], &n, &m) == 0) wa_domain_add_local(e, n, m);
    }
}

/* ------------------------------------------------------------------ */
/* domain predicates                                                  */
/* ------------------------------------------------------------------ */

static int is_lan(const wa_engine *e, uint32_t ip) {
    return in_cidr(ip, e->lan.net, e->lan.mask);
}

static int is_pinned(const wa_engine *e, uint32_t src, uint32_t dst) {
    for (int i = 0; i < e->domain.n_pins; i++) {
        if (in_cidr(src, e->domain.pins[i].src, e->domain.pins[i].smask) &&
            in_cidr(dst, e->domain.pins[i].dst, e->domain.pins[i].dmask))
            return 1;
    }
    return 0;
}

static int is_local(const wa_engine *e, uint32_t dst) {
    if (is_lan(e, dst)) return 1;
    for (int i = 0; i < e->domain.n_local; i++)
        if (in_cidr(dst, e->domain.local[i].net, e->domain.local[i].mask)) return 1;
    return 0;
}

static int is_vpn(const wa_engine *e, uint32_t dst) {
    for (int i = 0; i < e->domain.n_vpn; i++)
        if (in_cidr(dst, e->domain.vpn[i].net, e->domain.vpn[i].mask)) return 1;
    return 0;
}

/* ------------------------------------------------------------------ */
/* health                                                             */
/* ------------------------------------------------------------------ */

void wa_health_set_firmware(wa_engine *e, int unit, const wa_wan_facts *f) {
    if (unit < 0 || unit > 1 || !f) return;
    e->health.firmware[unit] = *f;
}

static wa_elig firmware_eligibility(const wa_wan_facts *f) {
    if (f->enable == 0) return WA_UNUSABLE;
    if (f->enable != 1) return WA_UNKNOWN;
    if (f->state_t < 0) return WA_UNKNOWN;
    if (f->state_t != 2) return WA_UNUSABLE;
    if (f->ip[0] == 0 || strcmp(f->ip, "0.0.0.0") == 0) return WA_UNUSABLE;
    if (f->gw[0] == 0 || strcmp(f->gw, "0.0.0.0") == 0) return WA_UNUSABLE;
    if (f->ifname[0] == 0) return WA_UNKNOWN;
    if (f->route_ready == 0) return WA_UNUSABLE;
    if (f->route_ready < 0) return WA_UNKNOWN;
    return WA_ELIGIBLE;
}

wa_elig wa_health_eligibility(wa_engine *e, int unit) {
    wa_health *h = &e->health;
    wa_elig firmware = firmware_eligibility(&h->firmware[unit]);
    if (firmware != WA_ELIGIBLE) return firmware;
    if (h->firmware_policy) return firmware_eligibility(&h->firmware[unit]);
    if (h->enhanced && h->have_snap) return h->snap.state[unit];
    if (h->excluded[unit]) return WA_UNKNOWN; /* absence of enhanced is not recovery */
    return firmware_eligibility(&h->firmware[unit]);
}

void wa_health_refresh(wa_engine *e, double now) {
    wa_health *h = &e->health;
    h->enhanced = (h->have_snap && h->snap.valid &&
                   (now - h->snap_mono) <= WA_SNAPSHOT_MAX_AGE)
                      ? 1
                      : 0;
}

void wa_health_switch_firmware(wa_engine *e) {
    e->health.firmware_policy = 1;
    e->health.excluded[0] = 0;
    e->health.excluded[1] = 0;
}

static wa_elig map_state(const char *v) {
    if (strcmp(v, "eligible") == 0) return WA_ELIGIBLE;
    if (strcmp(v, "unusable") == 0) return WA_UNUSABLE;
    return WA_UNKNOWN;
}

int wa_health_apply_snapshot(wa_engine *e, const char *text, double now) {
    wa_health *h = &e->health;
    if (!text || !*text) {
        h->have_snap = 0;
        h->enhanced = 0;
        return 0;
    }
    char version[16] = "", publisher[32] = "", boot[64] = "";
    double published = -1;
    long long seq = -1;
    int have_state[2] = {0, 0}, have_elig[2] = {0, 0}, have_rec[2] = {0, 0};
    int elig[2] = {0, 0}, rec[2] = {0, 0};
    wa_elig st[2] = {WA_UNKNOWN, WA_UNKNOWN};

    const char *p = text;
    while (*p) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        if (len > 250) return 0;
        char line[251];
        memcpy(line, p, len);
        line[len] = 0;
        p = nl ? nl + 1 : p + len;
        if (line[0] == 0 || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) return 0;
        *eq = 0;
        const char *k = line;
        const char *v = eq + 1;
        if (strcmp(k, "version") == 0) snprintf(version, sizeof version, "%s", v);
        else if (strcmp(k, "publisher") == 0) snprintf(publisher, sizeof publisher, "%s", v);
        else if (strcmp(k, "publisher_boot_id") == 0) snprintf(boot, sizeof boot, "%s", v);
        else if (strcmp(k, "seq") == 0) seq = atoll(v);
        else if (strcmp(k, "published_mono") == 0) published = atof(v);
        else if ((strncmp(k, "wan0.", 5) == 0) || (strncmp(k, "wan1.", 5) == 0)) {
            int u = (k[3] == '0') ? 0 : 1;
            const char *sub = k + 5;
            if (strcmp(sub, "state") == 0) {
                st[u] = map_state(v);
                have_state[u] = 1;
            } else if (strcmp(sub, "eligible") == 0) {
                elig[u] = atoi(v) != 0;
                have_elig[u] = 1;
            } else if (strcmp(sub, "recovered") == 0) {
                rec[u] = atoi(v) != 0;
                have_rec[u] = 1;
            }
        }
    }
    if (strcmp(version, "1") || strcmp(publisher, "wan-watchdog") || boot[0] == 0 ||
        seq < 0 || published < 0)
        return 0;
    if (!have_state[0] || !have_state[1] || !have_elig[0] || !have_elig[1] ||
        !have_rec[0] || !have_rec[1]) return 0;
    if (published > now || now - published > WA_SNAPSHOT_MAX_AGE) return 0;
    if (h->have_seq && !strcmp(h->snap.boot_id, boot) &&
        (unsigned long long)seq < h->last_seq) return 0;
    for(int u=0;u<2;u++)if(elig[u] && st[u]!=WA_ELIGIBLE)return 0;
    h->snap.valid = 1;
    h->snap.seq = (unsigned long long)seq;
    h->snap.published_mono = published;
    snprintf(h->snap.boot_id, sizeof h->snap.boot_id, "%s", boot);
    for (int u = 0; u < 2; u++) {
        h->snap.state[u] = (st[u] == WA_ELIGIBLE && !elig[u]) ? WA_UNKNOWN : st[u];
        h->snap.eligible[u] = elig[u];
        h->snap.recovered[u] = rec[u];
        h->excluded[u] = elig[u] ? 0 : 1;
    }
    h->have_snap = 1;
    h->snap_mono = published;
    h->enhanced = 1;
    h->last_seq = (unsigned long long)seq;
    h->have_seq = 1;
    return 1;
}

/* ------------------------------------------------------------------ */
/* association / state tables                                         */
/* ------------------------------------------------------------------ */

static wa_assoc *assoc_find(wa_engine *e, uint32_t src) {
    for (int i = 0; i < e->n_assoc; i++)
        if (e->assoc[i].src == src) return &e->assoc[i];
    return NULL;
}

static wa_assoc *assoc_add(wa_engine *e, uint32_t src, int wan, int pref, double now,
                           const char *reason) {
    if (e->n_assoc >= WA_MAX_ASSOC || e->next_binding_id >= WA_BINDING_LEGACY-1) return NULL;
    wa_assoc *a = &e->assoc[e->n_assoc++];
    memset(a, 0, sizeof *a);
    a->src = src;
    a->binding_id = ++e->next_binding_id;
    a->wan = wan;
    a->preferred = pref;
    a->reason = reason;
    a->created = now;
    return a;
}

static int is_transitional(const wa_engine *e, uint32_t src) {
    for (int i = 0; i < e->n_transitional; i++)
        if (e->transitional[i] == src) return 1;
    return 0;
}

static void add_transitional(wa_engine *e, uint32_t src) {
    if (is_transitional(e, src)) return;
    if (e->n_transitional < WA_MAX_ASSOC) e->transitional[e->n_transitional++] = src;
}

static void remove_transitional(wa_engine *e, uint32_t src) {
    for (int i = 0; i < e->n_transitional; i++) {
        if (e->transitional[i] == src) {
            memmove(&e->transitional[i], &e->transitional[i + 1],
                    (size_t)(e->n_transitional - i - 1) * sizeof(uint32_t));
            e->n_transitional--;
            return;
        }
    }
}

int wa_state_relevant(const wa_engine *e, const wa_state *st) {
    if (st->dir_reply) return 0;
    if (st->proto != 6 && st->proto != 17) return 0;
    if (!is_lan(e, st->src)) return 0;
    if (is_pinned(e, st->src, st->dst)) return 0;
    if (is_local(e, st->dst)) return 0;
    if (is_vpn(e, st->dst)) return 0;
    return 1;
}

static int assoc_has_relevant(wa_engine *e, uint32_t src) {
    wa_assoc *a = assoc_find(e, src);
    for (int i = 0; i < e->n_state; i++)
        if (a && e->state[i].src == src && e->state[i].binding_id == a->binding_id &&
            wa_state_relevant(e, &e->state[i])) return 1;
    return 0;
}

static int source_has_relevant(const wa_engine *e, uint32_t src) {
    for (int i = 0; i < e->n_state; i++)
        if (e->state[i].src == src && wa_state_relevant(e, &e->state[i])) return 1;
    return 0;
}

static void claim_source(wa_engine *e, uint32_t src, uint64_t binding_id) {
    for (int i = 0; i < e->n_state; i++)
        if (e->state[i].src == src && !e->state[i].binding_id && wa_state_relevant(e, &e->state[i]))
            e->state[i].binding_id = binding_id;
}

static void state_put(wa_engine *e, uint64_t ctid, uint32_t src, uint32_t dst, uint32_t sport,
                      uint32_t dport, uint8_t proto, int wan, int provisional, int dir_reply,
                      double now) {
    for (int i = 0; i < e->n_state; i++) {
        if (e->state[i].ctid == ctid) {
            wa_state *s = &e->state[i];
            if(s->src!=src || s->dst!=dst || s->sport!=sport || s->dport!=dport || s->proto!=proto)
                 { e->tuple_index_valid=0; s->binding_id=0; s->kernel_id=0; }
            s->src = src;
            s->dst = dst;
            s->sport = sport;
            s->dport = dport;
            s->proto = proto;
            s->wan = (int8_t)wan;
            s->provisional = (uint8_t)provisional;
            s->dir_reply = (uint8_t)(dir_reply ? 1 : 0);
            s->updated = now;
            if (provisional) { s->created = now; s->kernel_id = 0; }
            return;
        }
    }
    if (e->n_state >= WA_MAX_STATES) {
        e->uncertain = 1;
        snprintf(e->last_error, sizeof e->last_error, "state-table-full");
        return;
    }
    wa_state *s = &e->state[e->n_state++];
    memset(s, 0, sizeof *s);
    s->ctid = ctid;
    s->src = src;
    s->dst = dst;
    s->sport = sport;
    s->dport = dport;
    s->proto = proto;
    s->wan = (int8_t)wan;
    s->provisional = (uint8_t)provisional;
    s->dir_reply = (uint8_t)(dir_reply ? 1 : 0);
    s->created = now;
    s->updated = now;
    if(e->tuple_index_valid) {
        const unsigned mask=WA_MAX_STATES*2-1;
        unsigned slot=(unsigned)wa_ctid_hash(proto,src,dst,sport,dport)&mask;
        while(e->tuple_index[slot])slot=(slot+1)&mask;
        e->tuple_index[slot]=e->n_state;
    }
}

static void state_remove(wa_engine *e, uint64_t ctid) {
    for (int i = 0; i < e->n_state; i++) {
        if (e->state[i].ctid == ctid) {
            memmove(&e->state[i], &e->state[i + 1],
                    (size_t)(e->n_state - i - 1) * sizeof(wa_state));
            e->n_state--;
            e->tuple_index_valid=0;
            return;
        }
    }
}

uint64_t wa_flow_token(wa_engine *e, uint8_t proto, uint32_t src, uint32_t dst,
                       uint32_t sport, uint32_t dport) {
    const unsigned mask=WA_MAX_STATES*2-1;
    if(!e->tuple_index_valid) {
        memset(e->tuple_index,0,sizeof e->tuple_index);
        for(int i=0;i<e->n_state;i++) {
            const wa_state *s=&e->state[i];
            unsigned slot=(unsigned)wa_ctid_hash(s->proto,s->src,s->dst,s->sport,s->dport)&mask;
            while(e->tuple_index[slot])slot=(slot+1)&mask;
            e->tuple_index[slot]=i+1;
        }
        e->tuple_index_valid=1;
    }
    unsigned slot=(unsigned)wa_ctid_hash(proto,src,dst,sport,dport)&mask;
    while(e->tuple_index[slot]) {
        const wa_state *s=&e->state[e->tuple_index[slot]-1];
        if(s->proto==proto && s->src==src && s->dst==dst && s->sport==sport && s->dport==dport)return s->ctid;
        slot=(slot+1)&mask;
    }
    /* Adapter-owned namespace, distinct from 32-bit kernel pointer IDs. */
    return UINT64_C(0x8000000000000000) | ++e->next_flow_token;
}

static void sync_candidates(wa_engine *e, double now) {
    for (int i = 0; i < e->n_assoc; i++) {
        wa_assoc *a = &e->assoc[i];
        if (assoc_has_relevant(e, a->src)) {
            a->have_candidate = 0;
            a->have_confirmed = 0;
        } else if (!a->have_candidate) {
            a->have_candidate = 1;
            a->candidate_since = now;
        }
    }
}

/* ------------------------------------------------------------------ */
/* selection                                                          */
/* ------------------------------------------------------------------ */

static unsigned long long next_rng(wa_engine *e) {
    unsigned long long x = e->rng;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    e->rng = x;
    return x;
}

static int affinity_preferred(const wa_engine *e, uint32_t src) {
    uint64_t h = WA_FNV_OFFSET;
    h = fnv_bytes(h, e->affinity_seed, strlen(e->affinity_seed));
    h = fnv_u32(h, src);
    int total = e->weight[0] + e->weight[1];
    if (total <= 0) total = 1;
    return (int)(h % (unsigned long long)total) < e->weight[0] ? 0 : 1;
}

static int preferred_or_none(const wa_engine *e, uint32_t src) {
    return e->mode == WA_AFFINITY ? affinity_preferred(e, src) : -1;
}

static int select_wan(wa_engine *e, const int *choices, int n, int prefer) {
    if (n <= 0) return -1;
    if (n == 1) return choices[0];
    if (prefer >= 0)
        for (int i = 0; i < n; i++)
            if (choices[i] == prefer) return prefer;
    int total = 0;
    for (int i = 0; i < n; i++) total += e->weight[choices[i]];
    if (total <= 0) total = 1;
    unsigned long long r = next_rng(e) % (unsigned long long)total;
    int acc = 0;
    for (int i = 0; i < n; i++) {
        acc += e->weight[choices[i]];
        if (r < (unsigned long long)acc) return choices[i];
    }
    return choices[n - 1];
}

/* ------------------------------------------------------------------ */
/* admission                                                          */
/* ------------------------------------------------------------------ */

wa_admission wa_admission_decision(wa_engine *e, double now, uint32_t src, uint32_t dst,
                                   uint8_t proto, uint32_t sport, uint32_t dport,
                                   uint32_t mark, uint64_t ctid) {
    wa_admission r;
    r.action = WA_ACT_PASS;
    r.wan = -1;
    r.out_mark = mark;
    r.reason = "pass";

    wa_health_refresh(e, now);

    if (mark & WA_WAN_MASK) {
        r.reason = "already-wan-marked";
        return r;
    }
    if (proto != 6 && proto != 17) {
        r.reason = "proto-unsupported";
        return r;
    }
    if (!is_lan(e, src)) {
        r.reason = "non-lan-source";
        return r;
    }
    if (is_pinned(e, src, dst)) {
        r.reason = "gui-pin";
        return r;
    }
    if (is_local(e, dst)) {
        r.reason = "local-or-lan-dest";
        return r;
    }
    if (is_vpn(e, dst)) {
        r.reason = "vpn-dest";
        return r;
    }

    if (e->n_state >= WA_MAX_STATES) {
        int known = 0;
        for (int i=0;i<e->n_state;i++) if(e->state[i].ctid==ctid)known=1;
        if(!known) {
            e->uncertain=1;
            snprintf(e->last_error,sizeof e->last_error,"state-table-full");
            r.reason="state-table-full";
            return r;
        }
    }

    wa_assoc *a = assoc_find(e, src);
    if (!a) {
        int choices[2], nc = 0;
        for (int u = 0; u < 2; u++)
            if (wa_health_eligibility(e, u) == WA_ELIGIBLE) choices[nc++] = u;
        if (nc == 0) {
            e->uncertain = 1;
            r.reason = "no-eligible-wan";
            return r;
        }
        int pref = preferred_or_none(e, src);
        int wan = select_wan(e, choices, nc, pref);
        a = assoc_add(e, src, wan, pref, now,
                      is_transitional(e, src) ? "selected-mixed" : "selected");
        if (!a) {
            e->uncertain = 1;
            r.reason = "table-full";
            return r;
        }
        claim_source(e, src, WA_BINDING_LEGACY);
        remove_transitional(e, src);
    } else {
        int pref = a->preferred;
        if (pref >= 0 && a->wan != pref && wa_health_eligibility(e, pref) == WA_ELIGIBLE) {
            a->wan = pref;
            a->epoch = ++e->epoch;
            a->reason = "affinity-return";
            a->last_change = now;
        } else if (wa_health_eligibility(e, a->wan) == WA_UNUSABLE) {
            int choices[2], nc = 0;
            for (int u = 0; u < 2; u++)
                if (u != a->wan && wa_health_eligibility(e, u) == WA_ELIGIBLE) choices[nc++] = u;
            if (nc == 0) {
                e->uncertain = 1;
                r.reason = "no-eligible-wan-for-failover";
                return r;
            }
            a->wan = select_wan(e, choices, nc, -1);
            a->epoch = ++e->epoch;
            a->failovers++;
            a->reason = "failover";
            a->last_change = now;
        }
        /* WA_ELIGIBLE or WA_UNKNOWN -> retain */
    }

    a->have_candidate = 0;
    a->have_confirmed = 0;
    state_put(e, ctid, src, dst, sport, dport, proto, a->wan, 1, 0, now);
    for (int i = 0; i < e->n_state; i++)
        if (e->state[i].ctid == ctid) e->state[i].binding_id = a->binding_id;
    e->admissions++;

    r.action = WA_ACT_MARK;
    r.wan = a->wan;
    r.reason = a->reason;
    r.out_mark = wa_apply_wan_mark(mark, a->wan);
    return r;
}

/* ------------------------------------------------------------------ */
/* lifecycle events                                                   */
/* ------------------------------------------------------------------ */

void wa_event_new(wa_engine *e, double now, const wa_state *st, int update) {
    (void)update;
    if (!st) return;
    state_put(e, st->ctid, st->src, st->dst, st->sport, st->dport, st->proto, st->wan, 0,
              st->dir_reply, now);
    sync_candidates(e, now);
}

void wa_event_destroy(wa_engine *e, double now, uint64_t ctid) {
    state_remove(e, ctid); /* idempotent for duplicate DESTROY */
    sync_candidates(e, now);
}

/* ------------------------------------------------------------------ */
/* reconcile                                                          */
/* ------------------------------------------------------------------ */

static int parse_row(const char *line, wa_state *st) {
    unsigned long long ctid;
    unsigned proto, a, b, c, d, e2, f, g, h2, markv, inb, ass;
    char dir[8];
    int end=0;
    int n = sscanf(line, "%llx %u %u.%u.%u.%u %u.%u.%u.%u %7s mark=0x%x inbound=%u assured=%u%n",
                   &ctid, &proto, &a, &b, &c, &d, &e2, &f, &g, &h2, dir, &markv, &inb, &ass,&end);
    if (n != 14) return -1;
    if (a > 255 || b > 255 || c > 255 || d > 255 || e2 > 255 || f > 255 || g > 255 || h2 > 255)
        return -1;
    memset(st, 0, sizeof *st);
    st->ctid = (uint64_t)ctid;
    st->proto = (uint8_t)proto;
    st->src = (a << 24) | (b << 16) | (c << 8) | d;
    st->dst = (e2 << 24) | (f << 16) | (g << 8) | h2;
    st->wan = (int8_t)wa_wan_from_mark(markv);
    st->dir_reply = (strcmp(dir, "orig") != 0);
    const char *tail=line+end;
    while(*tail==' ')tail++;
    if(*tail) {
        unsigned sp,dp;int consumed=0;
        if(sscanf(tail,"sport=%u dport=%u%n",&sp,&dp,&consumed)!=2 || tail[consumed] || sp>65535 || dp>65535)return -1;
        st->sport=sp;st->dport=dp;
    }
    return 0;
}

static int merge_view(wa_engine *e,double started,double applied,const wa_state *states,int np,int full_view);

int wa_reconcile(wa_engine *e, double now, const char *framed_dump, int read_ok) {
    if (!read_ok) {
        e->uncertain = 1;
        snprintf(e->last_error, sizeof e->last_error, "read-error");
        return -1;
    }
    static wa_state parsed[WA_MAX_STATES];
    int np = 0, count = -1, ended = 0, bad = 0, first = 1;
    const char *p = framed_dump ? framed_dump : "";
    while (*p && !bad) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        if (len > 250) {
            bad = 1;
            break;
        }
        char line[251];
        memcpy(line, p, len);
        line[len] = 0;
        p = nl ? nl + 1 : p + len;
        if (first) {
            size_t hl = strlen(WA_DUMP_HEADER);
            if (strncmp(line, WA_DUMP_HEADER, hl) != 0) {
                bad = 1;
                break;
            }
            char *end;long parsed=strtol(line+hl,&end,10);
            if (end==line+hl || *end || parsed<0 || parsed>WA_MAX_STATES) {
                bad = 1;
                break;
            }
            count=(int)parsed;
            first = 0;
            continue;
        }
        if (strcmp(line, WA_DUMP_END) == 0) {
            ended = 1;
            while(*p=='\n' || *p=='\r' || *p==' ' || *p=='\t')p++;
            if(*p)bad=1;
            break;
        }
        if (line[0] == 0) continue;
        if (np >= WA_MAX_STATES || parse_row(line, &parsed[np]) != 0) {
            bad = 1;
            break;
        }
        np++;
    }
    if (bad || !ended || count != np) {
        e->uncertain = 1;
        snprintf(e->last_error, sizeof e->last_error, "unparseable-or-truncated");
        return -1;
    }

    return merge_view(e,now,now,parsed,np,0);
}

int wa_reconcile_view(wa_engine *e,double started,double applied,const wa_state *states,int count,int read_ok) {
    if(!read_ok || count<0 || count>WA_MAX_STATES || (count && !states) || started>applied ||
       applied-started>e->max_reconcile_age) {
        e->uncertain=1;snprintf(e->last_error,sizeof e->last_error,"invalid-lifecycle-view");return -1;
    }
    return merge_view(e,started,applied,states,count,1);
}

static int merge_view(wa_engine *e,double started,double applied,const wa_state *states,int np,int full_view) {
    e->have_reconcile = 1;
    e->last_reconcile_mono = started;
    e->last_error[0] = 0;
    e->uncertain = 0;

    static wa_state merged[WA_MAX_STATES];
    int id_index[WA_MAX_STATES*2]={0};
    const unsigned mask=WA_MAX_STATES*2-1;
    int nm = 0;
    for (int i = 0; i < np && nm < WA_MAX_STATES; i++) {
        merged[nm]=states[i];merged[nm].updated=started;
        unsigned slot=(unsigned)(merged[nm].ctid^(merged[nm].ctid>>32))&mask;
        while(id_index[slot]) {
            if(merged[id_index[slot]-1].ctid==merged[nm].ctid) {
                e->uncertain=1;snprintf(e->last_error,sizeof e->last_error,"duplicate-view-identity");return -1;
            }
            slot=(slot+1)&mask;
        }
        id_index[slot]=++nm;
    }
    for (int i = 0; i < e->n_state; i++) {
        wa_state *s = &e->state[i];
        int found = -1;
        unsigned slot=(unsigned)(s->ctid^(s->ctid>>32))&mask;
        while(id_index[slot]) {
            if(merged[id_index[slot]-1].ctid==s->ctid){found=id_index[slot]-1;break;}
            slot=(slot+1)&mask;
        }
        int newer=full_view?s->updated>=started:s->updated>started;
        if (found >= 0) {
            if (newer) merged[found] = *s;
            else if (merged[found].src == s->src && merged[found].dst == s->dst &&
                     merged[found].proto == s->proto &&
                     (!full_view || (merged[found].sport == s->sport && merged[found].dport == s->dport &&
                      (!s->kernel_id || !merged[found].kernel_id || s->kernel_id == merged[found].kernel_id))))
                merged[found].binding_id = s->binding_id;
            continue;
        }
        if ((newer || (s->provisional && !full_view)) && nm >= WA_MAX_STATES) {
            e->uncertain=1;
            snprintf(e->last_error,sizeof e->last_error,"reconcile-capacity");
            return -1; /* retain the original table, especially pending packets */
        }
        if (newer) {
            merged[nm++] = *s;
        } else if (s->provisional && !full_view) {
            /* A queued/unconfirmed packet is not evidence of termination. Keep it
             * and report uncertainty; never discard it on a timer. */
            merged[nm++] = *s;
            if (applied - s->created > e->provisional_warning_seconds) {
                e->uncertain = 1;
                snprintf(e->last_error, sizeof e->last_error, "unconfirmed-admission");
            }
        }
    }
    memcpy(e->state, merged, (size_t)nm * sizeof(wa_state));
    e->n_state = nm;
    e->tuple_index_valid=0;

    sync_candidates(e, applied);
    for (int i = 0; i < e->n_assoc; i++) {
        wa_assoc *a = &e->assoc[i];
        if (!assoc_has_relevant(e, a->src) && !a->have_confirmed) {
            a->have_confirmed = 1;
            a->confirmed_empty = applied;
        }
    }
    for (int i = e->n_transitional - 1; i >= 0; i--)
        if (!source_has_relevant(e, e->transitional[i])) remove_transitional(e, e->transitional[i]);
    return 0;
}

void wa_reconstruct(wa_engine *e) {
    if (e->uncertain) return;
    struct {
        uint32_t src;
        int wans;
        int unattributed;
        int legacy;
    } g[WA_MAX_ASSOC];
    int ng = 0;
    for (int i = 0; i < e->n_state; i++) {
        wa_state *s = &e->state[i];
        if (!wa_state_relevant(e, s)) continue;
        int gi = -1;
        for (int j = 0; j < ng; j++)
            if (g[j].src == s->src) {
                gi = j;
                break;
            }
        if (gi < 0) {
            if (ng >= WA_MAX_ASSOC) continue;
            g[ng].src = s->src;
            g[ng].wans = 0;
            g[ng].unattributed = 0;
            g[ng].legacy = 0;
            gi = ng++;
        }
        if (s->wan < 0)
            g[gi].unattributed = 1;
        else
            g[gi].wans |= (1 << s->wan);
        if (s->binding_id) g[gi].legacy = 1;
    }
    double now = e->last_reconcile_mono;
    for (int j = 0; j < ng; j++) {
        uint32_t src = g[j].src;
        /* Live policy edits can bring a previously pinned/excluded source
         * into scope. Adopt only unassociated sources; mixed flows following
         * our own failover must never erase an established decision. */
        if (assoc_find(e, src)) { claim_source(e, src, WA_BINDING_LEGACY); continue; }
        if (g[j].legacy || g[j].unattributed || g[j].wans == 3) {
            add_transitional(e, src);
            claim_source(e, src, WA_BINDING_LEGACY);
            continue;
        }
        if (g[j].wans == 1 || g[j].wans == 2) {
            if (assoc_find(e, src)) continue;
            int wan = (g[j].wans == 1) ? 0 : 1;
            wa_assoc *a = assoc_add(e, src, wan, preferred_or_none(e, src), now, "reconstructed");
            if (a) { a->adopted = 1; claim_source(e, src, a->binding_id); }
            remove_transitional(e, src);
        }
    }
}

int wa_tick(wa_engine *e, double now) {
    if(e->uncertain)return 0;
    if (!e->have_reconcile || (now - e->last_reconcile_mono) > e->max_reconcile_age) {
        e->uncertain = 1;
        return 0;
    }
    int expired = 0;
    for (int i = e->n_assoc - 1; i >= 0; i--) {
        wa_assoc *a = &e->assoc[i];
        if (!a->have_candidate || !a->have_confirmed) continue;
        double deadline =
            a->candidate_since > a->confirmed_empty ? a->candidate_since : a->confirmed_empty;
        if (now - deadline >= e->idle_seconds) {
            remove_transitional(e, a->src);
            e->assoc[i] = e->assoc[e->n_assoc - 1];
            e->n_assoc--;
            expired++;
        }
    }
    return expired;
}

/* ------------------------------------------------------------------ */
/* status                                                             */
/* ------------------------------------------------------------------ */

static const char *elig_str(wa_elig s) {
    return s == WA_ELIGIBLE ? "eligible" : (s == WA_UNUSABLE ? "unusable" : "unknown");
}

void wa_engine_status(const wa_engine *e, char *buf, size_t len) {
    size_t used = 0;
    int unobserved=0,legacy=0;
    for(int i=0;i<e->n_state;i++){unobserved+=e->state[i].provisional!=0;legacy+=e->state[i].binding_id==WA_BINDING_LEGACY;}
    used += (size_t)snprintf(buf + used, len - used,
                             "mode=%s enhanced=%s uncertain=%s wan0=%s wan1=%s "
                             "assoc=%d states=%d unobserved=%d admissions=%zu last_error=%s legacy_states=%d\n",
                             e->mode == WA_AFFINITY ? "affinity" : "sticky",
                             e->health.enhanced ? "yes" : "no", e->uncertain ? "yes" : "no",
                             elig_str(wa_health_eligibility((wa_engine *)e, 0)),
                             elig_str(wa_health_eligibility((wa_engine *)e, 1)), e->n_assoc,e->n_state,unobserved,
                             e->admissions, e->last_error[0] ? e->last_error : "-",legacy);
    for (int i = 0; i < e->n_assoc && used < len; i++) {
        char ip[16];
        wa_ip_to_str(e->assoc[i].src, ip, sizeof ip);
        int members=0,old=0;
        for(int j=0;j<e->n_state;j++)if(e->state[j].src==e->assoc[i].src && wa_state_relevant(e,&e->state[j])) {
            members+=e->state[j].binding_id==e->assoc[i].binding_id;
            old+=e->state[j].binding_id==WA_BINDING_LEGACY;
        }
        used += (size_t)snprintf(buf + used, len - used,
                                 "src=%s wan=%d preferred=%d reason=%s failovers=%d pending=%s binding=%llu members=%d legacy=%d\n",
                                 ip, e->assoc[i].wan, e->assoc[i].preferred,
                                 e->assoc[i].reason, e->assoc[i].failovers,
                                 e->assoc[i].have_candidate ? "yes" : "no",
                                 (unsigned long long)e->assoc[i].binding_id,members,old);
    }
    if (e->n_transitional && used < len) {
        used += (size_t)snprintf(buf + used, len - used, "awaiting_new=%d\n", e->n_transitional);
    }
}
