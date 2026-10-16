#define _POSIX_C_SOURCE 200809L
#include "wa_worker.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

static void worker_death(int sig) {
    (void)sig;
    /* The collector and all its command children occupy their own group. */
    kill(-getpgrp(),SIGKILL);
    _exit(125);
}

void wa_worker_init(wa_worker *job) {memset(job,0,sizeof *job);job->fd=-1;}

int wa_worker_start(wa_worker *job,void *result,size_t size,wa_collect_fn fn,
                     void *argument,int close_fd,double now) {
    if(job->pid){errno=EBUSY;return -1;}
    int fds[2];if(pipe(fds))return -1;
    pid_t parent=getpid();pid_t pid=fork();
    if(pid<0){close(fds[0]);close(fds[1]);return -1;}
    if(!pid) {
        close(fds[0]);if(close_fd>=0)close(close_fd);
        if(setpgid(0,0))_exit(125);
        signal(SIGTERM,worker_death);signal(SIGINT,worker_death);
        signal(SIGPIPE,SIG_DFL);
#ifdef __linux__
        if(prctl(PR_SET_PDEATHSIG,SIGTERM))_exit(125);
#endif
        if(getppid()!=parent)worker_death(SIGTERM);
        fcntl(fds[1],F_SETFD,FD_CLOEXEC);
        if(fn(argument,result,size))_exit(126);
        size_t sent=0;
        while(sent<size) {
            ssize_t n=write(fds[1],(char*)result+sent,size-sent);
            if(n<0 && errno==EINTR)continue;
            if(n<=0)_exit(127);
            sent+=(size_t)n;
        }
        close(fds[1]);_exit(0);
    }
    close(fds[1]);
    wa_worker_init(job);job->pid=pid;job->fd=fds[0];job->expected=size;job->started=now;
    fcntl(job->fd,F_SETFD,FD_CLOEXEC);
    int flags=fcntl(job->fd,F_GETFL,0);
    if(flags<0 || fcntl(job->fd,F_SETFL,flags|O_NONBLOCK)) {wa_worker_cancel(job);return -1;}
    return 0;
}

void wa_worker_cancel(wa_worker *job) {
    if(!job->pid)return;
    job->cancelled=1;
    /* Group setup may not have completed yet; also address the exact child. */
    if(!job->reaped){kill(-job->pid,SIGKILL);kill(job->pid,SIGKILL);}
    if(job->fd>=0){close(job->fd);job->fd=-1;}
    job->eof=1;
}

int wa_worker_poll(wa_worker *job,void *result,double now,double timeout) {
    if(!job->pid)return -1;
    if(!job->cancelled && now-job->started>timeout)wa_worker_cancel(job);
    if(job->fd>=0 && !job->eof) {
        size_t remaining=job->expected-job->used;
        /* Read one extra byte after the expected payload to require actual EOF. */
        char extra;
        size_t amount=remaining>16384?16384:remaining;
        ssize_t got=read(job->fd,remaining?(char*)result+job->used:&extra,remaining?amount:1);
        if(got>0) {
            if(!remaining)wa_worker_cancel(job);
            else job->used+=(size_t)got;
        } else if(!got) {close(job->fd);job->fd=-1;job->eof=1;}
        else if(errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)wa_worker_cancel(job);
    }
    if(!job->reaped) {
        pid_t got=waitpid(job->pid,&job->exit_status,WNOHANG);
        if(got==job->pid)job->reaped=1;
        else if(got<0 && errno==ECHILD){job->reaped=1;job->cancelled=1;}
    }
    if(!job->reaped || !job->eof)return 0;
    int ok=!job->cancelled && WIFEXITED(job->exit_status) && !WEXITSTATUS(job->exit_status) && job->used==job->expected;
    int status=job->exit_status;
    wa_worker_init(job);job->exit_status=status;
    return ok?1:-1;
}
