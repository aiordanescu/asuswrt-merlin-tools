#include "wa_supervisor.h"
#include "wa_schedule.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    wa_stall_policy p={0};
    /* Normal heartbeats and arbitrarily deep, short bursts never restart. */
    for(int i=0;i<10000;i++)
        assert(!wa_stall_observe(&p,100,(uint64_t)i/50+1,1024,i*.02));
    p=(wa_stall_policy){0};
    assert(!wa_stall_observe(&p,100,1,1,0));
    for(int i=1;i<5;i++)assert(!wa_stall_observe(&p,100,1,1,i));
    assert(wa_stall_observe(&p,100,1,1,5)); /* a single packet is sufficient */
    assert(!wa_stall_observe(&p,100,2,1,6)); /* progress resets suspicion */
    assert(!wa_stall_observe(&p,101,2,1,20)); /* PID changes reset it too */
    p=(wa_stall_policy){0};
    assert(!wa_stall_observe(&p,100,0,0,0));
    assert(!wa_stall_observe(&p,100,0,0,600)); /* idle/missing status is safe */
    assert(!wa_stall_observe(&p,100,0,1,601));
    assert(!wa_stall_observe(&p,100,0,0,601.5)); /* brief queue clears */
    assert(!wa_stall_observe(&p,100,0,1,602));
    assert(wa_stall_observe(&p,100,0,1,603)); /* frozen before first traffic */
    assert(!wa_stall_failure(&p,10));assert(!wa_stall_failure(&p,30));
    assert(wa_stall_failure(&p,59)); /* no third automatic restart */
    assert(!wa_stall_failure(&p,100)); /* sliding window expires */

    wa_save_schedule s={0};
    assert(!wa_save_due(&s,0));
    for(int i=0;i<1024;i++)wa_save_dirty(&s);
    assert(wa_save_due(&s,0));wa_save_begin(&s,0);
    for(int i=0;i<1024;i++)wa_save_dirty(&s);
    assert(!wa_save_due(&s,10)); /* only one writer */
    wa_save_end(&s,1);assert(s.saved==1024 && s.revision==2048);
    assert(!wa_save_due(&s,4.99));assert(wa_save_due(&s,5));
    wa_save_begin(&s,5);wa_save_end(&s,0);
    assert(s.saved==1024 && !wa_save_due(&s,9.99)); /* failures rate limited */
    assert(wa_save_due(&s,10));wa_save_begin(&s,10);wa_save_end(&s,1);
    assert(!wa_save_due(&s,15));
    puts("checkpoint coalescing and stall policy: passed");return 0;
}
