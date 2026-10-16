#define _POSIX_C_SOURCE 200809L
#include "wa_worker.h"
#include <assert.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static int collect(void *arg,void *result,size_t size) {
    int mode=*(int*)arg;
    if(mode==1){struct timespec t={0,200000000};nanosleep(&t,NULL);}
    if(mode==2)return -1;
    if(mode==3){struct timespec t={2,0};nanosleep(&t,NULL);}
    memset(result,0x5a,size);return 0;
}
int main(void) {
    static unsigned char data[300000];wa_worker job;int modes[]={0,1,2,3};
    int checks=0;
    for(unsigned i=0;i<4;i++) {
        wa_worker_init(&job);memset(data,0,sizeof data);
        assert(!wa_worker_start(&job,data,sizeof data,collect,&modes[i],-1,now()));checks++;
        int ready=0,iterations=0;double max_call=0,started=now();
        while(!ready && now()-started<3) {
            struct pollfd p={job.fd,POLLIN,0};poll(&p,1,1);
            double a=now();ready=wa_worker_poll(&job,data,now(),i==3?.05:1);
            double elapsed=now()-a;if(elapsed>max_call)max_call=elapsed;
            iterations++;
        }
        assert(ready==(i<2?1:-1));checks++;
        assert(!job.pid && job.fd==-1);checks++;
        if(i<2){assert(data[0]==0x5a && data[sizeof data-1]==0x5a);checks++;}
        if(i==1){assert(iterations>20);checks++;} /* caller kept running while child slept */
        assert(max_call<.1);checks++; /* no blocking read or waitpid */
    }
    int mode=3;wa_worker_init(&job);
    assert(!wa_worker_start(&job,data,sizeof data,collect,&mode,-1,now()));checks++;
    wa_worker_cancel(&job);int rc=0;
    for(int i=0;i<200 && !rc;i++) {struct timespec t={0,1000000};nanosleep(&t,NULL);rc=wa_worker_poll(&job,data,now(),1);}
    assert(rc==-1 && !job.pid);checks++;
    printf("worker: %d checks, 0 failures\n",checks);return 0;
}
