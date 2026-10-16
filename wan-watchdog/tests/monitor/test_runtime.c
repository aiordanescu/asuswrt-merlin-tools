#include "ww_runtime.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>
int main(void) {
    ww_fact f[2]={{0}}, old={0};ww_flags flags[2]={{0}};ww_decision d={0};
    f[0].ip=42;f[0].enable=1;f[0].target[0]=99;strcpy(f[0].ifname,"eth0");
    flags[1].acceptable=1;ww_decision_capture(&d,0,1,100*WH_NSEC,f,flags);
    old=f[0];f[0].ip=43;flags[1].acceptable=0;
    assert(d.expected[0].ip==42 && d.flags[1].acceptable && d.sampled==100*WH_NSEC);
    assert(ww_fact_changed(&old,&f[0]));old=f[0];old.route[0]=1;
    assert(!ww_fact_changed(&old,&f[0]));old.target[1]=7;assert(ww_fact_changed(&old,&f[0]));
    int pending=1;d.mask[0]=d.mask[1]=1;ww_decision_invalidate(&d,&pending);
    assert(!pending && !d.restore && !d.mask[0] && !d.mask[1]);
    assert(ww_facts_fresh(100*WH_NSEC,110*WH_NSEC));
    assert(ww_facts_fresh(100*WH_NSEC,111*WH_NSEC));
    assert(!ww_facts_fresh(100*WH_NSEC,111*WH_NSEC+1));
    assert(!ww_facts_fresh(0,100*WH_NSEC) && !ww_facts_fresh(101*WH_NSEC,100*WH_NSEC));
    assert(ww_fact_age_ms(0,100*WH_NSEC)==-1 && ww_fact_age_ms(101,100)==-1);
    assert(ww_fact_age_ms(100*WH_NSEC,110*WH_NSEC)==10000);
    wh_state s={0};s.applied_failed=-1;unsigned error=0,unconfirmed=0;
    assert(!ww_routing_observed(&s,1,0,1,0,0,0,0,0,0,&error,&unconfirmed));
    assert(s.routing_error && unconfirmed && s.applied_failed==-1);
    ww_routing_observed(&s,1,0,2,0,0,0,0,0,1,&error,&unconfirmed);
    assert(s.routing_error && unconfirmed); /* Warm-up is no acknowledgement. */
    assert(ww_routing_observed(&s,1,0,2,0,1,0,0,0,2,&error,&unconfirmed));
    assert(!s.routing_error && !unconfirmed && s.applied_failed==0 && s.applied_generation==2);
    ww_routing_observed(&s,1,0,2,0,1,1,1,0,3,&error,&unconfirmed);
    assert(s.routing_error && s.applied_failed==0 && s.applied_generation==2);
    ww_routing_observed(&s,1,0,2,-1,1,0,1,-1,4,&error,&unconfirmed);
    assert(!s.routing_error && s.applied_failed==-1 && s.applied_generation==4);
    s.routing_error=1;ww_routing_observed(&s,0,0,0,-1,0,0,0,-1,4,&error,&unconfirmed);
    assert(!s.routing_error); /* A good shadow inspection clears a transient failure. */
    puts("watchdog runtime decisions: passed");return 0;
}
