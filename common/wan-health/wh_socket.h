#ifndef WH_SOCKET_H
#define WH_SOCKET_H
#include "wh_protocol.h"
#include <poll.h>
#define WH_SUBSCRIBERS 16
#define WH_SERVER_FDS (WH_SUBSCRIBERS + 1)
typedef struct {
    int fd;
    uint64_t sent, pending_since;
} wh_peer;
typedef struct {
    int listener;
    char path[108];
    wh_peer peer[WH_SUBSCRIBERS];
    uint8_t frame[WH_MAX_FRAME];
    size_t length;
    uint64_t seq;
    unsigned accepted, dropped;
} wh_server;
typedef struct {
    int fd, seen, connected;
    char path[108];
    uint8_t boot[16];
    wh_state state;
    uint64_t retry_ns;
    unsigned backoff, rejected;
} wh_client;
uint64_t wh_now(void);
int wh_boot(uint8_t boot[16]);
int wh_random(uint8_t value[16]);
int wh_server_open(wh_server *server, const char *path);
void wh_server_close(wh_server *server);
int wh_server_publish(wh_server *server, const wh_state *state, uint64_t now);
void wh_server_fds(wh_server *server, struct pollfd fds[WH_SERVER_FDS]);
void wh_server_step(wh_server *server, const struct pollfd fds[WH_SERVER_FDS], uint64_t now);
void wh_client_init(wh_client *client, const char *path);
/* Call in the parent loop, before packet admission. Bounded to 8 records. */
int wh_client_step(wh_client *client, uint64_t now);
void wh_client_close(wh_client *client, uint64_t now);
int wh_client_fresh(const wh_client *client, uint64_t now);
#endif
