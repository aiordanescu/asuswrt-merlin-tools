#include "wa_config_line.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>
int main(void) {
    const char *ok[]={"MODE=sticky # default"," CLIENT=\"192.168.1.100\" ","export ENABLED='1' # on","QUEUE_NUM=20880", "VALUE='a$b'", "EMPTY= # ok"};
    const char *values[]={"sticky","192.168.1.100","1","20880","a$b",""};
    for(unsigned i=0;i<sizeof ok/sizeof *ok;i++){char b[128],*k,*v;strcpy(b,ok[i]);assert(wa_config_line(b,&k,&v)==1 && !strcmp(v,values[i]));}
    const char *bad[]={"MODE =sticky","MODE= sticky","MODE=$MODE","MODE=\"$(false)\"","MODE=sticky; true","MODE='sticky", "MODE=\"sticky\"#comment", "MODE=sticky\\x", "MODE=`false`", "MODE=sticky\r\n"};
    for(unsigned i=0;i<sizeof bad/sizeof *bad;i++){char b[128],*k,*v;strcpy(b,bad[i]);assert(wa_config_line(b,&k,&v)==-1);}
    puts("literal configuration grammar: passed");return 0;
}
