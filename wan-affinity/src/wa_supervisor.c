#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "wa_supervisor.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

int wa_stall_observe(wa_stall_policy *p,long pid,uint64_t progress,
                     unsigned queued,double now) {
    if(pid!=p->pid || progress!=p->progress) {
        p->pid=pid;p->progress=progress;p->unchanged_since=now;p->pending=0;
    }
    if(!queued){p->pending=0;return 0;}
    if(!p->pending){p->pending=1;p->pending_since=now;}
    return now-p->unchanged_since>=WA_STALL_SECONDS &&
           now-p->pending_since>=WA_PENDING_SECONDS;
}
int wa_stall_failure(wa_stall_policy *p,double now) {
    int kept=0;
    for(int i=0;i<p->failures;i++)
        if(now-p->failure[i]<WA_RESTART_WINDOW)p->failure[kept++]=p->failure[i];
    if(kept<WA_RESTART_LIMIT)p->failure[kept++]=now;
    p->failures=kept;
    return kept>=WA_RESTART_LIMIT;
}

static volatile sig_atomic_t ending;
static void stop(int sig){(void)sig;ending=1;}
static double now(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec+t.tv_nsec/1e9;
}
static int read_text(const char *path,char *text,size_t cap) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return -1;
    ssize_t n=read(fd,text,cap-1);close(fd);
    if(n<0)return -1;
    text[n]=0;return (int)n;
}
static long read_pid(const char *path) {
    char b[64],*end;if(read_text(path,b,sizeof b)<=0)return 0;
    errno=0;long pid=strtol(b,&end,10);
    return !errno && pid>1 && (*end=='\n' || !*end)?pid:0;
}
static int helper_identity(long pid,const char *binary) {
    if(pid<=1 || kill((pid_t)pid,0))return 0;
    char path[80],cmd[256];snprintf(path,sizeof path,"/proc/%ld/cmdline",pid);
    return read_text(path,cmd,sizeof cmd)>0 && !strcmp(cmd,binary);
}
static int touch(const char *path) {
    int fd=open(path,O_WRONLY|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0)return -1;
    close(fd);return 0;
}
static pid_t controller_start(const char *controller,const char *command) {
    pid_t pid=fork();if(pid)return pid;
    execl("/bin/sh","sh",controller,command,(char *)NULL);_exit(127);
}
static void rotate_log(const char *run,const char *name) {
    char path[256];struct stat st;
    if(snprintf(path,sizeof path,"%s/%s",run,name)>=(int)sizeof path)return;
    if(!lstat(path,&st) && S_ISREG(st.st_mode) && st.st_size>=262144) {
        int fd=open(path,O_WRONLY|O_TRUNC|O_NOFOLLOW|O_CLOEXEC);
        if(fd>=0)close(fd);
    }
}
int wa_supervise(const char *controller,const char *run,const char *binary) {
    char mon[256],pidfile[256],status[256],stopped[256],trip[256],request[256];
    if(controller[0]!='/' || run[0]!='/' || binary[0]!='/' || strlen(run)>200)return 2;
    snprintf(mon,sizeof mon,"%s/supervisor.pid",run);
    snprintf(pidfile,sizeof pidfile,"%s/wan-affinity.pid",run);
    snprintf(status,sizeof status,"%s/status",run);
    snprintf(stopped,sizeof stopped,"%s/stopped",run);
    snprintf(trip,sizeof trip,"%s/supervisor-tripped",run);
    snprintf(request,sizeof request,"%s/restart-request",run);
    signal(SIGTERM,stop);signal(SIGINT,stop);
    wa_stall_policy policy={0};
    pid_t child=0;double next_reconcile=now()+10;int was_tripped=0,trip_cleanup=0;
    while(!ending) {
        struct timespec delay={1,0};nanosleep(&delay,NULL);
        if(ending || read_pid(mon)!=(long)getpid() || !access(stopped,F_OK))break;
        if(child && waitpid(child,NULL,WNOHANG)==child)child=0;
        if(!access(trip,F_OK)){
            if(!was_tripped)trip_cleanup=1;
            was_tripped=1;
            if(!child && trip_cleanup) {
                child=controller_start(controller,"recover-stop");trip_cleanup=0;
            }
            continue;
        }
        if(was_tripped){memset(&policy,0,sizeof policy);was_tripped=0;}
        double t=now();long pid=read_pid(pidfile);
        unsigned queued=0;int bound=0;char b[16384];uint64_t progress=0;
        int alive=helper_identity(pid,binary);
        FILE *f=fopen("/proc/net/netfilter/nfnetlink_queue","r");
        if(f) {
            while(fgets(b,sizeof b,f)) {
                unsigned q,n;long owner;
                if(sscanf(b,"%u %ld %u",&q,&owner,&n)==3 && owner==pid && alive) {
                    bound=1;queued=n;break;
                }
            }
            fclose(f);
        }
        if(read_text(status,b,sizeof b)>0) {
            char *p=strstr(b,"loop_mono_ns=");
            if(p)progress=strtoull(p+13,NULL,10);
        }
        /* Do not restart a healthy idle helper merely because its status could
         * not be written. A pending packet supplies evidence of impact. */
        int stalled=alive && bound && wa_stall_observe(&policy,pid,progress,queued,t);
        if(stalled) {
            int tripped=wa_stall_failure(&policy,t);
            if(touch(tripped?trip:request)) {
                fprintf(stderr,"supervisor: cannot record recovery marker; closing stuck queue anyway\n");
            }
            /* Recheck identity immediately before the signal. Queue closure
             * does not depend on a shell lock, collector or checkpoint writer. */
            if(read_pid(pidfile)==pid && helper_identity(pid,binary))kill((pid_t)pid,SIGKILL);
            fprintf(stderr,"supervisor: stalled pid=%ld queued=%u action=%s mono=%.3f\n",
                    pid,queued,tripped?"firmware-until-start":"restart",t);fflush(stderr);
            policy.pid=0;policy.pending=0;
            trip_cleanup=tripped;
            was_tripped=tripped;
            next_reconcile=t;
        }
        if(!alive && policy.pid){policy.pid=0;policy.pending=0;next_reconcile=t;}
        /* Exit bypass is provided by the kernel immediately. Reconcile removes
         * the admission jump and restores ordinary firmware traversal. */
        if(!child && (t>=next_reconcile || (!alive && policy.pid))) {
            rotate_log(run,"wan-affinity.log");rotate_log(run,"supervisor.log");
            child=controller_start(controller,!access(trip,F_OK)?"recover-stop":"reconcile");
            if(child>0)trip_cleanup=0;
            next_reconcile=t+10;
        }
    }
    return 0;
}
