#ifndef WW_RUNTIME_H
#define WW_RUNTIME_H
#include "ww_core.h"
#include <string.h>
/* Portable main-loop decisions; I/O and process ownership stay in the daemon. */
#define WW_FACT_MAX_AGE (11 * WH_NSEC)
typedef struct {
    int enable, active, route[2];
    uint32_t ip, gateway, target[2];
    char ifname[16];
} ww_fact;
typedef struct {
    int wanted, restore, mask[2];
    uint64_t sampled;
    ww_fact expected[2];
    ww_flags flags[2];
} ww_decision;
static inline int ww_fact_changed(const ww_fact *a,const ww_fact *b) {
    return a->enable!=b->enable || a->ip!=b->ip || a->gateway!=b->gateway ||
      memcmp(a->ifname,b->ifname,sizeof a->ifname) || memcmp(a->target,b->target,sizeof a->target);
}
static inline long long ww_fact_age_ms(uint64_t at,uint64_t now) {
    return !at || at>now ? -1LL : (long long)((now-at)/1000000);
}
static inline int ww_facts_fresh(uint64_t at,uint64_t now) {
    return at && at<=now && now-at<=WW_FACT_MAX_AGE;
}
static inline void ww_decision_capture(ww_decision *d,int wanted,int restore,uint64_t sampled,
                                      const ww_fact facts[2],const ww_flags flags[2]) {
    d->wanted=wanted;d->restore=restore;d->sampled=sampled;
    memcpy(d->expected,facts,sizeof d->expected);memcpy(d->flags,flags,sizeof d->flags);
}
static inline void ww_decision_invalidate(ww_decision *d,int *pending) {
    *pending=0;d->restore=0;memset(d->mask,0,sizeof d->mask);
}
static inline int ww_routing_observed(wh_state *s,int active,int pinned,int operation,int failed,
                                     int acted,int error,int restore,int wanted,uint64_t generation,
                                     unsigned *failure,unsigned *unconfirmed) {
    if(operation)*failure=(unsigned)error;
    if(operation==1 && failed>=0)*unconfirmed=1;
    int changed=0;
    if(acted && !error) {
        *unconfirmed=0;
        changed=generation!=s->applied_generation || (!pinned && s->applied_failed!=(restore?-1:wanted));
        if(!pinned)s->applied_failed=restore?-1:wanted;
        if(generation>=s->applied_generation)s->applied_generation=generation;
    }
    s->routing_error=active ? (*failure || *unconfirmed) : 0;
    return changed;
}
#endif
