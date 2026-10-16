#ifndef WA_POLICY_H
#define WA_POLICY_H
#include "wa_engine.h"
#include <stdio.h>
#include <string.h>

/* Return 1 for an unfamiliar routing policy, conservatively projected onto
 * source/destination selectors; 0 for firmware WAN/main tables; -1 if truncated.
 * Ignoring additional mark/interface predicates only broadens the exclusion. */
static int wa_policy_exclusion(const char *row, uint32_t *s, uint32_t *sm,
                               uint32_t *d, uint32_t *dm) {
    char copy[512], table[64]="", from[64]="all", to[64]="all";
    *s=*sm=*d=*dm=0;
    if(strlen(row)>=sizeof copy || !strchr(row,'\n'))return -1;
    snprintf(copy,sizeof copy,"%s",row);
    int inverted=0;
    for(char *token=strtok(copy," \t\n");token;token=strtok(NULL," \t\n")) {
        char *target=NULL;
        if(!strcmp(token,"not"))inverted=1;
        if(!strcmp(token,"lookup") || !strcmp(token,"table"))target=table;
        else if(!strcmp(token,"from"))target=from;
        else if(!strcmp(token,"to"))target=to;
        if(target) {
            char *value=strtok(NULL," \t\n");
            if(!value)return 1;
            snprintf(target,64,"%s",value);
        }
    }
    if(!strcmp(table,"main") || !strcmp(table,"local") || !strcmp(table,"default") ||
       !strcmp(table,"wan0") || !strcmp(table,"wan1") || !strcmp(table,"254") ||
       !strcmp(table,"255") || !strcmp(table,"253"))return 0;
    if(inverted || (strcmp(from,"all") && wa_parse_cidr(from,s,sm)) ||
       (strcmp(to,"all") && wa_parse_cidr(to,d,dm))) *s=*sm=*d=*dm=0;
    return 1;
}

/* A domain that cannot be represented exactly could mark local or
 * policy-routed traffic toward a WAN, so the helper then pauses placement. */
enum { WA_DOMAIN_OK=0, WA_DOMAIN_LOCAL_CAPACITY, WA_DOMAIN_PIN_CAPACITY,
       WA_DOMAIN_UNSUPPORTED_PIN, WA_DOMAIN_TRUNCATED_RULE };

static const char *wa_domain_status_name(int status) {
    switch(status) {
    case WA_DOMAIN_OK: return "ok";
    case WA_DOMAIN_LOCAL_CAPACITY: return "local-capacity";
    case WA_DOMAIN_PIN_CAPACITY: return "pin-capacity";
    case WA_DOMAIN_UNSUPPORTED_PIN: return "unsupported-pin";
    default: return "truncated-rule";
    }
}

/* Firmware GUI pins from nvram wans_routing_rulelist: <src>dst>unit... The list
 * is modified in place. Returns a WA_DOMAIN_* status for the first problem. */
static int wa_policy_add_gui_pins(wa_engine *e, char *list) {
    int status=WA_DOMAIN_OK;
    for(char *tok=strtok(list,"<");tok;tok=strtok(NULL,"<")) {
        char *gt=strchr(tok,'>');
        if(!gt)continue;
        *gt=0;
        char *destination=gt+1,*end=strchr(destination,'>');
        if(end)*end=0;
        uint32_t s=0,sm=0,d=0,dm=0;
        int bad=0;
        if(*tok && strcmp(tok,"all"))bad=wa_parse_cidr(tok,&s,&sm);
        if(*destination && strcmp(destination,"all"))bad|=wa_parse_cidr(destination,&d,&dm);
        if(bad){if(!status)status=WA_DOMAIN_UNSUPPORTED_PIN;continue;}
        if(wa_domain_add_pin(e,s,sm,d,dm)<0 && !status)status=WA_DOMAIN_PIN_CAPACITY;
    }
    return status;
}

/* One `ip -4 route show table main` row. Non-prefix rows (default, nexthop
 * continuations) are not destinations and are ignored. */
static int wa_policy_add_route(wa_engine *e, const char *row) {
    char cidr[64];uint32_t n,mask;
    if(sscanf(row,"%63s",cidr)!=1 || !strcmp(cidr,"default"))return WA_DOMAIN_OK;
    if(wa_parse_cidr(cidr,&n,&mask) || !mask)return WA_DOMAIN_OK;
    return wa_domain_add_local(e,n,mask)<0?WA_DOMAIN_LOCAL_CAPACITY:WA_DOMAIN_OK;
}

/* One `ip -4 rule show` row: unfamiliar tables become exclusions. */
static int wa_policy_add_rule(wa_engine *e, const char *row) {
    uint32_t s=0,sm=0,d=0,dm=0;
    int excluded=wa_policy_exclusion(row,&s,&sm,&d,&dm);
    if(excluded<0)return WA_DOMAIN_TRUNCATED_RULE;
    if(!excluded)return WA_DOMAIN_OK;
    return wa_domain_add_pin(e,s,sm,d,dm)<0?WA_DOMAIN_PIN_CAPACITY:WA_DOMAIN_OK;
}
#endif
