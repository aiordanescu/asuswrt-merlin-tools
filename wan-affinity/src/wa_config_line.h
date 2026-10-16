#ifndef WA_CONFIG_LINE_H
#define WA_CONFIG_LINE_H
#include <ctype.h>
#include <stddef.h>
#include <string.h>
/* Shared, literal subset of shell KEY=VALUE syntax. No expansion, command
 * execution or multiline values. Return 1 assignment, 0 blank/comment, -1 bad. */
static inline int wa_config_line(char *s,char **key,char **value) {
    if(strchr(s,'\r'))return -1; /* Shell sourcing does not strip CRLF. */
    while(isspace((unsigned char)*s))s++;
    if(!*s || *s=='#')return 0;
    if(!strncmp(s,"export",6) && isspace((unsigned char)s[6])) {
        s+=6;while(isspace((unsigned char)*s))s++;
    }
    *key=s;
    if(!isalpha((unsigned char)*s) && *s!='_')return -1;
    while(isalnum((unsigned char)*s) || *s=='_')s++;
    char *end=s;
    while(*s==' ' || *s=='\t')s++;
    /* Shell assignments cannot have whitespace before or after '='. */
    if(s!=end || *s!='=')return -1;
    *s++=0;*value=s;char quote=0,*out=s;
    if(*s=='\'' || *s=='"'){quote=*s++;*value=out=s;}
    while(*s && *s!='\r' && *s!='\n') {
        if(quote && *s==quote){s++;quote=0;if(*s && !isspace((unsigned char)*s))return -1;break;}
        if(!quote && isspace((unsigned char)*s))break;
        if((!quote || quote=='"') && strchr("$`\\",*s))return -1;
        if(!quote && strchr(";|&()<>\"'",*s))return -1;
        *out++=*s++;
    }
    if(quote)return -1;
    while(isspace((unsigned char)*s))s++;
    if(*s && *s!='#')return -1;
    *out=0;return 1;
}
#endif
