#ifndef WA_WORKER_H
#define WA_WORKER_H
#include <stddef.h>
#include <sys/types.h>
typedef int (*wa_collect_fn)(void *argument,void *result,size_t capacity);
typedef struct {
    pid_t pid;
    int fd,eof,reaped,exit_status,cancelled;
    size_t used,expected;
    double started;
} wa_worker;
void wa_worker_init(wa_worker *job);
int wa_worker_start(wa_worker *job,void *result,size_t size,wa_collect_fn fn,
                     void *argument,int close_fd,double now);
/* Never blocks: one bounded pipe read and WNOHANG. 1 complete, -1 failed, 0 pending. */
int wa_worker_poll(wa_worker *job,void *result,double now,double timeout);
void wa_worker_cancel(wa_worker *job);
#endif
