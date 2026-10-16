#ifndef WA_SCHEDULE_H
#define WA_SCHEDULE_H
#include <stdint.h>

/* Five-second maximum write frequency. A fork owns an immutable snapshot;
 * completing it must never acknowledge changes made later by the parent. */
typedef struct {
    uint64_t revision, saved, writing;
    double next;
} wa_save_schedule;
static inline void wa_save_dirty(wa_save_schedule *s) { s->revision++; }
static inline int wa_save_due(const wa_save_schedule *s,double now) {
    return !s->writing && s->revision!=s->saved && now>=s->next;
}
static inline void wa_save_begin(wa_save_schedule *s,double now) {
    s->writing=s->revision;s->next=now+5.0;
}
static inline void wa_save_end(wa_save_schedule *s,int success) {
    if(success)s->saved=s->writing;
    s->writing=0;
}
#endif
