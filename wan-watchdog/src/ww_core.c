#include "ww_core.h"
#include <string.h>

int ww_clock_gap(uint64_t mono, uint64_t boot, uint64_t previous_mono, uint64_t previous_boot) {
    if (mono < previous_mono || boot < previous_boot)
        return 1;
    uint64_t elapsed = mono - previous_mono, awake = boot - previous_boot;
    return elapsed > 5 * WH_NSEC || (awake > elapsed && awake - elapsed > WH_NSEC);
}

void ww_reset(ww_core *c, int u, uint64_t now) {
    ww_wan *w = &c->wan[u];
    int excluded = w->excluded;
    wh_wan health = w->health;
    memset(w, 0, sizeof *w);
    w->excluded = excluded;
    w->health = health;
    w->window_start = now;
    w->health.config++;
    w->health.reason = WH_WARMING;
    w->health.bad = w->health.acceptable = w->health.eligible = w->health.recovered = 0;
    w->health.excluded = (unsigned)excluded;
    w->health.recovery = excluded ? WH_HELD : WH_NOT_REQUIRED;
    w->health.measured_ns = w->health.valid_ns = w->health.recovery_since_ns = 0;
    for (int t = 0; t < 2; t++) {
        w->health.samples[t] = w->health.lost[t] = w->health.unknown[t] = 0;
        w->health.latest[t] = -1;
    }
}
void ww_init(ww_core *c, uint64_t now) {
    memset(c, 0, sizeof *c);
    for (int u = 0; u < 2; u++) {
        c->wan[u].health.unit = (unsigned)u;
        ww_reset(c, u, now);
    }
}
static void append(ww_wan *w, int t, uint64_t now, int result) {
    unsigned kept = 0;
    for (unsigned i = 0; i < w->count[t]; i++)
        if (now >= w->samples[t][i].time && now - w->samples[t][i].time < 30 * WH_NSEC)
            w->samples[t][kept++] = w->samples[t][i];
    if (kept == WW_SAMPLES) {
        kept = 0;
        result = -1;
    } /* impossible at the supported 2s cadence */
    w->samples[t][kept++] = (ww_sample){now, result};
    w->count[t] = kept;
}
void ww_sample_cycle(ww_core *c, int u, uint64_t now, int enabled, int a, int b, int active,
                     ww_flags *f) {
    ww_wan *w = &c->wan[u];
    wh_wan *h = &w->health;
    memset(f, 0, sizeof *f);
    /* Missing cycles must not silently accrue continuous outage/recovery. */
    if (w->last_sample && (now < w->last_sample || now - w->last_sample > 5 * WH_NSEC)) {
        w->have_down = w->have_route_recovery = w->have_admission_recovery = 0;
    }
    w->last_sample = now;
    int results[2] = {a, b},
        ready = now >= w->window_start && now - w->window_start >= 30 * WH_NSEC;
    for (int t = 0; t < 2; t++) {
        append(w, t, now, results[t]);
        unsigned lost = 0, unknown = 0;
        for (unsigned i = 0; i < w->count[t]; i++) {
            lost += w->samples[t][i].result == 0;
            unknown += w->samples[t][i].result < 0;
        }
        unsigned n = w->count[t];
        f->samples[t] = (int)n;
        f->loss[t] = n ? (int)((100 * lost + n - 1) / n) : 100;
        if (n < 10 || now - w->samples[t][0].time < 26 * WH_NSEC || unknown)
            ready = 0;
        h->samples[t] = n;
        h->lost[t] = lost;
        h->unknown[t] = unknown;
        h->latest[t] = results[t];
    }
    f->reason = WH_WARMING;
    int clean = 0;
    if (enabled == 0) {
        f->bad = 1;
        f->reason = WH_DISABLED;
    } else if (enabled != 1 || a < 0 || b < 0)
        f->reason = WH_UNKNOWN;
    else {
        if (ready) {
            f->reason = WH_USABLE;
            f->acceptable = f->loss[0] <= 10 && f->loss[1] <= 10 && a == 1 && b == 1;
            clean = f->loss[0] <= 5 && f->loss[1] <= 5 && a == 1 && b == 1;
            if (f->loss[0] >= 30 && f->loss[1] >= 30) {
                f->bad = 1;
                f->reason = WH_DEGRADED;
            }
        }
        if (a == 0 && b == 0) {
            if (!w->have_down) {
                w->down_since = now;
                w->have_down = 1;
            }
            if (now - w->down_since >= 12 * WH_NSEC) {
                f->bad = 1;
                f->reason = WH_UNREACHABLE;
            }
        } else
            w->have_down = 0;
    }
    if (f->reason == WH_UNKNOWN || f->reason == WH_DISABLED)
        w->have_down = 0;
    if (active && clean) {
        if (!w->have_route_recovery) {
            w->route_recovery = now;
            w->have_route_recovery = 1;
        }
        f->recovered = now - w->route_recovery >= 120 * WH_NSEC;
    } else
        w->have_route_recovery = 0;
    if (f->bad)
        w->excluded = 1;
    if (w->excluded && clean) {
        if (!w->have_admission_recovery) {
            w->admission_recovery = now;
            w->have_admission_recovery = 1;
        }
        if (now - w->admission_recovery >= 120 * WH_NSEC)
            w->excluded = 0;
    } else
        w->have_admission_recovery = 0;
    if (h->reason != (unsigned)f->reason || h->excluded != (unsigned)w->excluded)
        h->transition++;
    h->reason = (unsigned)f->reason;
    h->bad = (unsigned)f->bad;
    h->acceptable = (unsigned)f->acceptable;
    h->recovered = (unsigned)f->recovered;
    h->excluded = (unsigned)w->excluded;
    h->eligible = (unsigned)(f->acceptable && !w->excluded);
    h->recovery = w->excluded                  ? WH_HELD
                  : w->have_admission_recovery ? WH_COMPLETE
                                               : WH_NOT_REQUIRED;
    h->recovery_since_ns = w->have_admission_recovery ? w->admission_recovery : 0;
    h->measured_ns = now;
    if (enabled == 1 && a >= 0 && b >= 0)
        h->valid_ns = now;
}
int ww_whole_intent(const ww_flags f[2], int failed, int *restore) {
    int wanted = failed;
    *restore = 0;
    if (f[0].bad && f[1].acceptable)
        wanted = 0;
    else if (f[1].bad && f[0].acceptable)
        wanted = 1;
    if (wanted >= 0 && wanted == failed && f[wanted].recovered) {
        *restore = 1;
        return -1;
    }
    return wanted;
}
int ww_pin_intent(const ww_flags f[2], int u, int active, int *destination, int *new_active) {
    *new_active = active;
    *destination = u;
    if (active) {
        if (f[u].recovered) {
            *new_active = 0;
            return 1;
        }
        if (f[1 - u].acceptable) {
            *destination = 1 - u;
            return 1;
        }
    } else if (f[u].bad) {
        if (f[1 - u].acceptable) {
            *destination = 1 - u;
            *new_active = 1;
            return 1;
        }
    } else if (f[u].acceptable)
        return 1;
    return 0;
}
void ww_admission_gate(ww_core *c, const ww_flags f[2], const int active[2], int failed, int error,
                       int whole) {
    for (int u = 0; u < 2; u++)
        if (active[u]) {
            c->wan[u].excluded = 1;
            c->wan[u].health.excluded = 1;
            c->wan[u].health.eligible = 0;
            c->wan[u].health.recovery = WH_HELD;
        }
    /* Existing emergency reversal may use an acceptable destination before
     * its usual recovery hold. Release only after the overlay is confirmed. */
    if (whole && failed >= 0 && !error && f[1 - failed].acceptable) {
        int u = 1 - failed;
        c->wan[u].excluded = 0;
        c->wan[u].health.excluded = 0;
        c->wan[u].health.eligible = 1;
        c->wan[u].health.recovery = WH_NOT_REQUIRED;
    }
}
