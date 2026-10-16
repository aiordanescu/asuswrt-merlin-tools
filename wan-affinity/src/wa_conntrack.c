#define _POSIX_C_SOURCE 200809L
#include "wa_conntrack.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

static int number(const char *text,unsigned long long max,uint64_t *out) {
    char *end;errno=0;
    unsigned long long value=strtoull(text,&end,0);
    if(errno || end==text || (*end && strcmp(end,"\n")) || value>max || *text=='-')return -1;
    *out=value;return 0;
}

static int parse_native(const char *line,wa_ct_record *out,int unconfirmed) {
    memset(out,0,sizeof *out);
    char work[1024];
    if(strlen(line)>=sizeof work)return -1;
    snprintf(work,sizeof work,"%s",line);
    int src=0,dst=0,sp=0,dp=0,id=0,bad=0;
    char *save=NULL;
    for(char *t=strtok_r(work," \t\r\n",&save);t;t=strtok_r(NULL," \t\r\n",&save)) {
        uint64_t value=0;
        if(!strcmp(t,"tcp"))out->proto=6;
        else if(!strcmp(t,"udp"))out->proto=17;
        else if(!strncmp(t,"src=",4) && !src) {src=1;bad|=wa_parse_ipv4(t+4,&out->src)!=0;}
        else if(!strncmp(t,"dst=",4)) {
            if(!dst){dst=1;bad|=wa_parse_ipv4(t+4,&out->dst)!=0;}
            else {out->have_reply_dst=1;bad|=wa_parse_ipv4(t+4,&out->reply_dst)!=0;}
        }
        else if(!strncmp(t,"sport=",6) && !sp){sp=1;bad|=number(t+6,65535,&value)!=0;out->sport=(uint32_t)value;}
        else if(!strncmp(t,"dport=",6) && !dp){dp=1;bad|=number(t+6,65535,&value)!=0;out->dport=(uint32_t)value;}
        else if(!strncmp(t,"mark=",5)){bad|=number(t+5,UINT32_MAX,&value)!=0;out->mark=(uint32_t)value;}
        else if(!strncmp(t,"id=",3)){id=1;bad|=number(t+3,UINT32_MAX,&out->kernel_id)!=0;}
        else if(!strncmp(t,"zone=",5)){bad|=number(t+5,UINT16_MAX,&value)!=0 || value!=0;}
    }
    if(!out->proto)return 0;
    out->unconfirmed=unconfirmed;
    return bad || !src || !dst || !sp || !dp || (!unconfirmed && (!id || !out->kernel_id)) ? -1 : 1;
}

int wa_parse_native_conntrack(const char *line,wa_ct_record *out) {return parse_native(line,out,0);}
int wa_parse_unconfirmed_conntrack(const char *line,wa_ct_record *out) {return parse_native(line,out,1);}

int wa_ct_view_add(wa_ct_view *view,const wa_ct_record *row) {
    const unsigned mask=WA_MAX_STATES*2-1;
    unsigned slot=(unsigned)wa_ctid_hash(row->proto,row->src,row->dst,row->sport,row->dport)&mask;
    while(view->slots[slot]) {
        wa_ct_record *old=&view->rows[view->slots[slot]-1];
        if(old->proto==row->proto && old->src==row->src && old->dst==row->dst &&
           old->sport==row->sport && old->dport==row->dport) {
            if(!row->unconfirmed)*old=*row;
            return 0;
        }
        slot=(slot+1)&mask;
    }
    if(view->count>=WA_MAX_STATES)return -1;
    view->rows[view->count]=*row;view->slots[slot]=++view->count;return 0;
}

int wa_ct_egress(const wa_ct_record *row,const wa_health *health) {
    /* Native GUI source pins outrank fwmarks: an old pin can therefore leave a
     * misleading WAN connmark. A confirmed SNAT reply address is better proof. */
    if(row->have_reply_dst)for(int u=0;u<2;u++) {
        uint32_t wan_ip;
        if(!wa_parse_ipv4(health->native[u].ip,&wan_ip) && row->reply_dst==wan_ip)return u;
    }
    return wa_wan_from_mark(row->mark);
}
