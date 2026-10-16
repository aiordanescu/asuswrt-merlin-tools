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
    int overflow;   /* rows that did not fit; nonzero means the view is partial */
} wa_ct_view;
/* 0: recorded or merged; -1: view full (counted in overflow). */
int wa_ct_view_add(wa_ct_view *view,const wa_ct_record *row);
/* Records only rows the engine can place (wa_state_relevant under e's domain):
 * DNS to the router, LAN, local, pinned and VPN traffic never consume capacity.
 * 1: outside placement scope; otherwise as wa_ct_view_add. */
int wa_ct_view_add_relevant(wa_ct_view *view,const wa_engine *e,const wa_ct_record *row);
#endif
