#ifndef WW_CORE_H
#define WW_CORE_H
#include "wh_protocol.h"
#define WW_SAMPLES 64
typedef struct {
    uint64_t time;
    int result;
} ww_sample;
typedef struct {
    ww_sample samples[2][WW_SAMPLES];
    unsigned count[2];
    uint64_t window_start, down_since, route_recovery, admission_recovery, last_sample;
    int have_down, have_route_recovery, have_admission_recovery, excluded;
    wh_wan health;
} ww_wan;
typedef struct {
    ww_wan wan[2];
} ww_core;
typedef struct {
    int bad, acceptable, recovered, loss[2], samples[2], reason;
} ww_flags;
void ww_init(ww_core *core, uint64_t now);
void ww_reset(ww_core *core, int unit, uint64_t now);
void ww_sample_cycle(ww_core *core, int unit, uint64_t dispatch, int enabled, int a, int b,
                     int active, ww_flags *flags);
int ww_whole_intent(const ww_flags flags[2], int failed, int *restore);
int ww_pin_intent(const ww_flags flags[2], int unit, int active, int *destination, int *new_active);
void ww_admission_gate(ww_core *core, const ww_flags flags[2], const int active[2],
                       int applied_failed, int error, int whole_lan);
int ww_clock_gap(uint64_t mono, uint64_t boot, uint64_t previous_mono, uint64_t previous_boot);
#endif
