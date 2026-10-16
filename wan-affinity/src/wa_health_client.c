#define _POSIX_C_SOURCE 200809L
#include "wa_health_client.h"
#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
void wa_health_merge_firmware(wa_engine *e, const wa_health *collected) {
    memcpy(e->health.firmware, collected->firmware, sizeof e->health.firmware);
}
static void save(wa_health_feed *f, const wa_health *health) {
    wh_state checkpoint = f->client.state;
    wh_state *s = &checkpoint;
    int mask = health->excluded[0] | (health->excluded[1] << 1);
    if (mask == f->checkpoint_mask)
        return;
    for (int u = 0; u < 2; u++) {
        s->wan[u].excluded = (unsigned)health->excluded[u];
        if (health->excluded[u])
            s->wan[u].eligible = 0;
    }
    uint8_t b[WH_MAX_FRAME];
    size_t n = wh_encode(b, sizeof b, s);
    char tmp[220];
    snprintf(tmp, sizeof tmp, "%s.new", f->checkpoint);
    FILE *out = fopen(tmp, "w");
    if (!out)
        return;
    int bad = fwrite(b, 1, n, out) != n;
    if (fclose(out))
        bad = 1;
    if (!bad && !rename(tmp, f->checkpoint))
        f->checkpoint_mask = mask;
}
void wa_health_feed_init(wa_health_feed *f, wa_engine *e, enum wa_health_source source,
                         const char *socket_path, const char *runtime) {
    memset(f, 0, sizeof *f);
    f->source = source;
    f->checkpoint_mask = -1;
    wh_client_init(&f->client,
                   source == WA_HEALTH_FIRMWARE || source == WA_HEALTH_LEGACY ? "" : socket_path);
    snprintf(f->checkpoint, sizeof f->checkpoint, "%s/health.cache", runtime);
    e->health.firmware_policy = source == WA_HEALTH_FIRMWARE;
    if (source == WA_HEALTH_WATCHDOG)
        e->health.excluded[0] = e->health.excluded[1] = 1;
    if (source == WA_HEALTH_FIRMWARE || source == WA_HEALTH_LEGACY)
        return;
    FILE *in = fopen(f->checkpoint, "r");
    if (!in)
        return;
    uint8_t b[WH_MAX_FRAME];
    size_t n = fread(b, 1, sizeof b, in);
    int bad = ferror(in);
    fclose(in);
    wh_state s;
    if (bad || wh_decode(&s, b, n) || memcmp(s.boot, f->client.boot, 16))
        return;
    f->client.seen = 1;
    f->client.state = s;
    for (int u = 0; u < 2; u++)
        e->health.excluded[u] = source == WA_HEALTH_WATCHDOG ? 1 : (int)s.wan[u].excluded;
}
int wa_health_feed_step(wa_health_feed *f, wa_engine *e, uint64_t now) {
    if (f->source == WA_HEALTH_FIRMWARE || f->source == WA_HEALTH_LEGACY)
        return 0;
    int rc = wh_client_step(&f->client, now);
    wa_health *h = &e->health;
    if (wh_client_fresh(&f->client, now)) {
        const wh_state *s = &f->client.state;
        h->have_snap = h->enhanced = h->snap.valid = 1;
        h->snap_mono = (double)s->published_ns / 1e9;
        h->snap.seq = s->seq;
        h->snap.published_mono = h->snap_mono;
        for (int u = 0; u < 2; u++) {
            const wh_wan *w = &s->wan[u];
            uint32_t ip = 0;
            int recent =
                w->measured_ns && now >= w->measured_ns && now - w->measured_ns <= WH_MAX_AGE_NS;
            int same_firmware = inet_pton(AF_INET, h->firmware[u].ip, &ip) == 1 && ip == w->ip &&
                              !strcmp(h->firmware[u].ifname, w->ifname);
            h->snap.state[u] = recent && same_firmware && w->bad        ? WA_UNUSABLE
                               : recent && same_firmware && w->eligible ? WA_ELIGIBLE
                                                                      : WA_UNKNOWN;
            h->snap.eligible[u] = w->eligible && recent && same_firmware;
            h->snap.recovered[u] = w->recovered;
            if (recent && same_firmware)
                h->excluded[u] = !w->eligible;
        }
        save(f, h);
    } else {
        h->have_snap = h->enhanced = 0;
        if (f->source == WA_HEALTH_WATCHDOG)
            h->excluded[0] = h->excluded[1] = 1;
    }
    return rc;
}
