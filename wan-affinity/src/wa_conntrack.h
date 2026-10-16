#ifndef WA_CONNTRACK_H
#define WA_CONNTRACK_H
#include "wa_engine.h"
typedef struct {
    uint8_t proto;
    uint32_t src,dst,sport,dport,reply_dst,mark;
    uint64_t kernel_id;
    int have_reply_dst;
    int unconfirmed;
} wa_ct_record;
/* 1: complete supported row; 0: other protocol; -1: malformed/unsupported zone. */
int wa_parse_firmware_conntrack(const char *line,wa_ct_record *out);
int wa_parse_unconfirmed_conntrack(const char *line,wa_ct_record *out);
int wa_ct_egress(const wa_ct_record *row,const wa_health *health);
typedef struct {
    wa_ct_record rows[WA_MAX_STATES];
    int slots[WA_MAX_STATES*2];
    int count;
} wa_ct_view;
int wa_ct_view_add(wa_ct_view *view,const wa_ct_record *row);
#endif
