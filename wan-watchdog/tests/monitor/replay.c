#include "ww_core.h"
#include <stdio.h>
int main(void) {
    ww_core c;
    ww_init(&c, WH_NSEC);
    unsigned sec;
    int u, en, active, a, b, reset;
    while (scanf("%u %d %d %d %d %d %d", &sec, &u, &en, &active, &a, &b, &reset) == 7) {
        if (u < 0 || u > 1)
            return 1;
        if (reset)
            ww_reset(&c, u, (uint64_t)sec * WH_NSEC);
        ww_flags f;
        ww_sample_cycle(&c, u, (uint64_t)sec * WH_NSEC, en, a, b, active, &f);
        printf("%d %d %d %s %d %d %d %d\n", f.bad, f.acceptable, f.recovered,
               wh_reason_name((unsigned)f.reason), f.loss[0], f.loss[1], f.samples[0],
               f.samples[1]);
    }
    return 0;
}
