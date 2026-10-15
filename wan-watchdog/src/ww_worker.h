#ifndef WW_WORKER_H
#define WW_WORKER_H
#include <stddef.h>
#include <sys/types.h>
typedef int (*ww_collect_fn)(void *argument, void *result, size_t capacity);
typedef struct {
    pid_t pid;
    int fd, eof, reaped, exit_status, cancelled;
    size_t used, expected;
    double started;
} ww_worker;
void ww_worker_init(ww_worker *job);
int ww_worker_start(ww_worker *job, void *result, size_t size, ww_collect_fn fn, void *argument,
                    int close_fd, double now);
/* Never blocks: one bounded pipe read and WNOHANG. 1 complete, -1 failed, 0 pending. */
int ww_worker_poll(ww_worker *job, void *result, double now, double timeout);
void ww_worker_cancel(ww_worker *job);
#endif
