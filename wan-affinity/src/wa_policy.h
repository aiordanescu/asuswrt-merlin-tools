#ifndef WA_POLICY_H
#define WA_POLICY_H
#include "wa_engine.h"
#include <stdio.h>
#include <string.h>

/* Return 1 for an unfamiliar routing policy, conservatively projected onto
 * source/destination selectors; 0 for native WAN/main tables; -1 if truncated.
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
#endif
