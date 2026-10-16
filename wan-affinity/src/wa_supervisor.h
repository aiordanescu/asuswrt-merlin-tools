#ifndef WA_SUPERVISOR_H
#define WA_SUPERVISOR_H
#include <stdint.h>

#define WA_STALL_SECONDS 5.0
#define WA_PENDING_SECONDS 1.0
#define WA_RESTART_WINDOW 60.0
#define WA_RESTART_LIMIT 3
typedef struct {
    uint64_t progress;
    long pid;
    double unchanged_since, pending_since;
    double failure[WA_RESTART_LIMIT];
    int failures, pending;
} wa_stall_policy;
/* Observe progress independently of its timestamp. Only a continuously pending
 * queue and five seconds without progress can trigger recovery. One is enough. */
int wa_stall_observe(wa_stall_policy *,long pid,uint64_t progress,
                     unsigned queued,double now);
int wa_stall_failure(wa_stall_policy *,double now);
int wa_supervise(const char *controller,const char *run,const char *binary);
#endif
