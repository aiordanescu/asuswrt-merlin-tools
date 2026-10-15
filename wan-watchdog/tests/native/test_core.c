#include "ww_core.h"
#include <stdio.h>
#include <string.h>
static int checks, failures;
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        checks++;                                                                                  \
        if (!(x)) {                                                                                \
            fprintf(stderr, "line %d: %s\n", __LINE__, #x);                                        \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)
static ww_flags sample(ww_core *c, unsigned sec, int a, int b, int active) {
    ww_flags f;
    ww_sample_cycle(c, 0, (uint64_t)sec * WH_NSEC, 1, a, b, active, &f);
    return f;
}
int main(void) {
    CHECK(!ww_clock_gap(3 * WH_NSEC, 3 * WH_NSEC, WH_NSEC, WH_NSEC));
    CHECK(ww_clock_gap(3 * WH_NSEC, 5 * WH_NSEC, WH_NSEC, WH_NSEC));
    CHECK(ww_clock_gap(7 * WH_NSEC, 7 * WH_NSEC, WH_NSEC, WH_NSEC));
    CHECK(ww_clock_gap(WH_NSEC, 3 * WH_NSEC, 2 * WH_NSEC, 2 * WH_NSEC));
    ww_core c;
    ww_init(&c, WH_NSEC);
    ww_flags f = {0};
    for (unsigned t = 1; t <= 31; t += 2) {
        f = sample(&c, t, 1, 1, 0);
        if (t < 31)
            CHECK(!f.acceptable);
    }
    CHECK(f.acceptable && !f.bad && c.wan[0].health.eligible);
    for (unsigned t = 33; t <= 45; t += 2)
        f = sample(&c, t, 0, 0, 0);
    CHECK(f.bad && f.reason == WH_UNREACHABLE && c.wan[0].excluded);
    for (unsigned t = 47; t <= 193; t += 2)
        f = sample(&c, t, 1, 1, 1);
    CHECK(!c.wan[0].health.eligible); /* Loss history first clears, then 120s. */
    f = sample(&c, 195, 1, 1, 1);
    CHECK(f.recovered && c.wan[0].health.eligible);
    f = sample(&c, 199, -1, 1, 1);
    CHECK(!f.recovered && f.reason == WH_UNKNOWN && !f.acceptable);
    ww_init(&c, WH_NSEC);
    for (unsigned t = 1; t <= 151; t += 2)
        f = sample(&c, t, 0, 1, 0);
    CHECK(!f.bad); /* One broken target must not fail a WAN. */
    ww_init(&c, WH_NSEC);
    for (unsigned t = 1; t <= 13; t += 2)
        f = sample(&c, t, 0, 0, 0);
    CHECK(f.bad);
    ww_reset(&c, 0, 15 * WH_NSEC);
    CHECK(c.wan[0].excluded);
    for (unsigned t = 15; t <= 165; t += 2)
        f = sample(&c, t, 1, 1, 0);
    CHECK(c.wan[0].health.eligible && !f.recovered); /* No-overlay recovery. */
    ww_flags pair[2] = {{.bad = 1}, {.acceptable = 1}};
    int restore;
    CHECK(ww_whole_intent(pair, -1, &restore) == 0 && !restore);
    pair[0].bad = 0;
    pair[0].recovered = 1;
    CHECK(ww_whole_intent(pair, 0, &restore) == -1 && restore);
    pair[1].bad = 1;
    pair[1].acceptable = 0;
    pair[0].acceptable = 1;
    CHECK(ww_whole_intent(pair, 0, &restore) == 1 && !restore);
    pair[0].acceptable = pair[1].acceptable = pair[0].recovered = 0;
    CHECK(ww_whole_intent(pair, 0, &restore) == 0 && !restore);
    ww_core gates;
    ww_init(&gates, WH_NSEC);
    int markers[2] = {1, 0};
    pair[0].acceptable = 1;
    pair[1].acceptable = 1;
    ww_admission_gate(&gates, pair, markers, -1, 1, 1);
    CHECK(gates.wan[0].excluded && !gates.wan[0].health.eligible);
    markers[0] = 0;
    markers[1] = 1;
    ww_admission_gate(&gates, pair, markers, 1, 1, 1);
    CHECK(gates.wan[0].excluded); /* Failed reversal is never an acknowledgement. */
    ww_admission_gate(&gates, pair, markers, 1, 0, 1);
    CHECK(!gates.wan[0].excluded && gates.wan[0].health.eligible);
    wh_state s = {0}, d;
    s.seq = 1;
    s.published_ns = 166 * WH_NSEC;
    s.progress = 2;
    s.desired_failed = s.applied_failed = -1;
    s.instance[0] = 1;
    s.wan[1].unit = 1;
    s.wan[0] = c.wan[0].health;
    uint8_t b[WH_MAX_FRAME];
    size_t n = wh_encode(b, sizeof b, &s);
    CHECK(n == 384 && !wh_decode(&d, b, n));
    CHECK(!wh_validate(&d, s.boot, 166 * WH_NSEC));
    CHECK(wh_validate(&d, s.boot, 182 * WH_NSEC));
    CHECK(wh_validate(&d, s.boot, 165 * WH_NSEC)); /* Future publication. */
    uint8_t other_boot[16] = {1};
    CHECK(wh_validate(&d, other_boot, 166 * WH_NSEC));
    d.wan[0].valid_ns = WH_NSEC;
    CHECK(wh_validate(&d, s.boot, 166 * WH_NSEC));
    CHECK(!wh_decode(&d, b, n));
    d.wan[0].measured_ns = 200 * WH_NSEC;
    CHECK(wh_validate(&d, s.boot, 166 * WH_NSEC));
    CHECK(!wh_decode(&d, b, n));
    d.seq = 0;
    CHECK(wh_validate(&d, s.boot, 166 * WH_NSEC));
    for (size_t i = 0; i < n; i++)
        CHECK(wh_decode(&d, b, i));
    b[3] ^= 1;
    CHECK(wh_decode(&d, b, n));
    b[3] ^= 1;
    s.wan[0].eligible = 1;
    s.wan[0].bad = 1;
    n = wh_encode(b, sizeof b, &s);
    CHECK(wh_decode(&d, b, n));
    uint8_t uuid[16];
    CHECK(!wh_uuid("046fcf09-66c7-402c-85f4-186b49235730", uuid));
    CHECK(wh_uuid("short", uuid));
    CHECK(wh_uuid("z46fcf09-66c7-402c-85f4-186b49235730", uuid));
    printf("native core/codec: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
