#ifndef WW_PROBE_H
#define WW_PROBE_H
#include "wh_protocol.h"
typedef struct {
    int fd;
    uint32_t source, target;
    uint64_t token;
    uint16_t id, sequence;
    int result;
} ww_probe;
int ww_probe_open(ww_probe *probe, uint32_t source, const char *ifname);
int ww_probe_error_result(int error);
void ww_probe_send(ww_probe *probe, uint32_t target, uint64_t token, uint16_t sequence);
void ww_probe_receive(ww_probe *probe, int accept_reply);
#endif
