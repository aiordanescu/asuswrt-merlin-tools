#define _GNU_SOURCE
#include "wh_socket.h"
#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <sys/wait.h>
static int checks;
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        checks++;                                                                                  \
        if (!(x)) {                                                                                \
            fprintf(stderr, "line %d: %s\n", __LINE__, #x);                                        \
            abort();                                                                               \
        }                                                                                          \
    } while (0)
static void server_tick(wh_server *s) {
    struct pollfd p[WH_SERVER_FDS];
    wh_server_fds(s, p);
    poll(p, WH_SERVER_FDS, 1);
    wh_server_step(s, p, wh_now());
}
static void state_init(wh_state *s) {
    memset(s, 0, sizeof *s);
    CHECK(!wh_boot(s->boot));
    CHECK(!wh_random(s->instance));
    s->seq = 1;
    s->published_ns = wh_now();
    s->desired_failed = s->applied_failed = -1;
    for (int u = 0; u < 2; u++) {
        wh_wan *w = &s->wan[u];
        w->unit = (unsigned)u;
        w->reason = WH_USABLE;
        w->eligible = w->acceptable = 1;
        w->measured_ns = w->valid_ns = s->published_ns;
        strcpy(w->ifname, "lo");
        inet_pton(AF_INET, "127.0.0.1", &w->ip);
    }
}
static void refresh(wh_server *s, wh_state *state) {
    state->seq++;
    state->published_ns = wh_now();
    for (int u = 0; u < 2; u++)
        state->wan[u].measured_ns = state->wan[u].valid_ns = state->published_ns;
    CHECK(!wh_server_publish(s, state, wh_now()));
}
static int peers(const wh_server *s) {
    int n=0;for(int i=0;i<WH_SUBSCRIBERS;i++)n+=s->peer[i].fd>=0;return n;
}
static void limits_and_credentials(wh_server *s,const char *dir,const char *path) {
    struct sockaddr_un a={.sun_family=AF_UNIX};strcpy(a.sun_path,path);
    int fd[WH_SUBSCRIBERS+1];
    for(int i=0;i<=WH_SUBSCRIBERS;i++) {
        fd[i]=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_NONBLOCK|SOCK_CLOEXEC,0);CHECK(fd[i]>=0);
        CHECK(!connect(fd[i],(void*)&a,sizeof a));server_tick(s);
    }
    CHECK(peers(s)==WH_SUBSCRIBERS && s->accepted==WH_SUBSCRIBERS);
    char b[WH_MAX_FRAME];CHECK(recv(fd[WH_SUBSCRIBERS],b,sizeof b,0)==0);
    CHECK(recv(fd[0],b,sizeof b,0)>0);CHECK(send(fd[0],"x",1,MSG_NOSIGNAL)==1);
    server_tick(s);CHECK(peers(s)==WH_SUBSCRIBERS-1);
    int received=(int)recv(fd[0],b,sizeof b,0);
    CHECK(received==0 || (received<0 && errno==ECONNRESET));
    for(int i=0;i<=WH_SUBSCRIBERS;i++)close(fd[i]);server_tick(s);
    CHECK(!peers(s));
    if(geteuid()==0) {
        /* Temporarily permit path traversal to reach the credential check. */
        CHECK(!chmod(dir,0755) && !chmod(path,0666));
        pid_t child=fork();CHECK(child>=0);
        if(!child) {
            if(setgid(65534) || setuid(65534))_exit(3);
            int c=socket(AF_UNIX,SOCK_SEQPACKET,0);
            if(c<0 || connect(c,(void*)&a,sizeof a))_exit(4);
            char frame[WH_MAX_FRAME];int n=(int)recv(c,frame,sizeof frame,0);close(c);_exit(n==0?0:5);
        }
        int status=0,done=0;
        for(int i=0;i<200 && !done;i++){server_tick(s);done=waitpid(child,&status,WNOHANG)==child;}
        CHECK(done && WIFEXITED(status) && !WEXITSTATUS(status));
        CHECK(!peers(s) && s->accepted==WH_SUBSCRIBERS);
        CHECK(!chmod(dir,0700) && !chmod(path,0600));
    } else puts("SKIP different-UID credential test: root required");
}
int main(void) {
    char dir[100], path[108];
    snprintf(dir, sizeof dir, "%s/wh-test-%ld", getenv("WH_TEST_ROOT") ? getenv("WH_TEST_ROOT") : "/tmp", (long)getpid());
    CHECK(!mkdir(dir, 0700));
    snprintf(path, sizeof path, "%s/s", dir);
    wh_server server;
    CHECK(!wh_server_open(&server, path));
    wh_state state;
    state_init(&state);
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    wh_client a, b;
    wh_client_init(&a, path);
    wh_client_init(&b, path);
    for (int i = 0; i < 20; i++) {
        wh_client_step(&a, wh_now());
        wh_client_step(&b, wh_now());
        server_tick(&server);
    }
    CHECK(a.connected == 2 && b.connected == 2);
    CHECK(a.state.seq == 1 && b.state.seq == 1);
    int slow = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    CHECK(slow >= 0);
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    strcpy(addr.sun_path, path);
    CHECK(!connect(slow, (void *)&addr, sizeof addr));
    server_tick(&server);
    for (int i = 0; i < 300; i++) {
        refresh(&server, &state);
        server_tick(&server);
        wh_client_step(&a, wh_now());
        wh_client_step(&b, wh_now());
        CHECK(a.state.seq == state.seq && b.state.seq == state.seq);
    }
    int pending = 0;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        pending += server.peer[i].pending_since > 0;
    CHECK(pending == 1);
    struct pollfd p[WH_SERVER_FDS];
    wh_server_fds(&server, p);
    poll(p, WH_SERVER_FDS, 0);
    wh_server_step(&server, p, wh_now() + WH_MAX_AGE_NS + WH_NSEC);
    CHECK(server.dropped == 1);
    close(slow);
    wh_server other;
    CHECK(wh_server_open(&other, path)); /* Preserve a live endpoint. */
    wh_server_close(&server);
    wh_client_step(&a, wh_now());
    CHECK(a.fd < 0);
    CHECK(!wh_server_open(&server, path));
    state_init(&state);
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    a.retry_ns = 0;
    for (int i = 0; i < 20; i++) {
        wh_client_step(&a, wh_now());
        server_tick(&server);
    }
    CHECK(a.connected == 2 && a.state.seq == 1); /* New instance, reset sequence. */
    /* Identical-sequence replay inside a connection is rejected. */
    wh_client_step(&a, wh_now());
    CHECK(a.state.seq == state.seq);
    uint64_t rejected = a.rejected;
    for (int i = 0; i < WH_SUBSCRIBERS; i++)
        if (server.peer[i].fd >= 0)
            server.peer[i].sent = 0;
    CHECK(!wh_server_publish(&server, &state, wh_now()));
    wh_client_step(&a, wh_now());
    CHECK(a.rejected == rejected + 1 && !wh_client_fresh(&a, wh_now()));
    /* One WAN's stale evidence cannot be hidden by fresh publications. */
    state.wan[1].measured_ns = state.wan[1].valid_ns = 1;
    CHECK(wh_validate(&state, state.boot, wh_now()));
    /* Boot changes and unsupported frames never replace the last good view. */
    wh_state decoded;
    uint8_t packet[WH_MAX_FRAME];
    size_t length = wh_encode(packet, sizeof packet, &state);
    CHECK(length > 0);
    packet[7] = 99;
    CHECK(wh_decode(&decoded, packet, length));
    wh_client_close(&a, wh_now());
    wh_client_close(&b, wh_now());
    wh_server_close(&server);
    CHECK(!wh_server_open(&server,path));
    state_init(&state);CHECK(!wh_server_publish(&server,&state,wh_now()));
    limits_and_credentials(&server,dir,path);wh_server_close(&server);
    CHECK(!rmdir(dir));
    printf("health socket: %d checks, 0 failures\n", checks);
    return 0;
}
