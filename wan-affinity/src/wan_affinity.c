/*
 * wan-affinity on-router helper.
 *
 * Single-threaded event loop over:
 *   - the NFQUEUE netlink fd        (serialized admission)
 *   - a nonblocking worker pipe    (complete U-then-C lifecycle snapshots)
 *
 * Only the bounded worker executes firmware-fact/conntrack commands. Admissions
 * and snapshot application remain serialized in the parent event loop.
 *
 * The decision engine lives in wa_engine.c and has no Linux dependency. This
 * file is the platform adapter. It is intentionally built only for the router;
 * the host test suite exercises wa_engine.c instead.
 *
 * Packet path (mangle table, owned chain WA_ADMIT, jumped to before the
 * firmware `-j balance` rule):
 *
 *   -A WA_ADMIT -m conntrack --ctstatus CONFIRMED -j RETURN
 *   -A WA_ADMIT -m connmark ! --mark 0x0/0xf0000000 -j RETURN
 *   -A WA_ADMIT -m mark --mark 0x0/0xf0000000 \
 *        -j NFQUEUE --queue-num N --queue-bypass
 *   -A WA_ADMIT -m mark --mark 0xa0000000/0xf0000000 -j MARK --set-xmark 0x0/0xf0000000
 *   -A WA_ADMIT -m mark --mark 0x80000000/0xf0000000 -j CONNMARK --save-mark \
 *        --nfmask 0xf0000000 --ctmask 0xf0000000
 *   -A WA_ADMIT -m mark --mark 0x90000000/0xf0000000 -j CONNMARK --save-mark \
 *        --nfmask 0xf0000000 --ctmask 0xf0000000
 *
 * The NF_REPEAT verdict (disproving the earlier NF_ACCEPT recipe) re-enters the
 * table; the WAN/pass guard prevents requeueing and lets the save run in the
 * same pass. The installer is wan-affinity.sh; this binary only decides.
 */
#define _GNU_SOURCE
#include "wa_engine.h"
#include "wa_checkpoint.h"
#include "wa_conntrack.h"
#include "wa_nl_frame.h"
#include "wa_worker.h"
#include "wa_health_client.h"
#include "wa_policy.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <limits.h>
#include <linux/netfilter.h>
#include <linux/netfilter/nfnetlink.h>
#include <linux/netfilter/nfnetlink_queue.h>
#include <linux/netfilter/nfnetlink_conntrack.h>
#include <linux/netlink.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define WA_VERSION "0.4.3"
static wa_health_feed health_feed;

static volatile sig_atomic_t stopping;
static uint32_t sequence;

static unsigned long queue_packets,queue_passes,missing_ctid,tcp_admissions,udp_admissions;
static double last_refresh_ms,max_refresh_ms,max_timestamp_delay_us;
static unsigned long refresh_ok,refresh_failed;
static int kernel_unconfirmed;
static double last_refresh_success;
static pid_t refresh_pid;

static void on_signal(int sig) {
    (void)sig;
    stopping = 1;
}

static double mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void die(const char *what) {
    perror(what);
    exit(1);
}

/* ------------------------------------------------------------------ */
/* config                                                             */
/* ------------------------------------------------------------------ */

typedef struct {
    wa_mode mode;
    int queue_num;
    int queue_maxlen;
    double idle_seconds;
    double max_reconcile_seconds;
    double provisional_warning_seconds;
    double reconcile_interval;
    char affinity_seed[65];
    char snapshot_path[160];
    char health_socket[108];
    enum wa_health_source health_source;
    char status_dir[120];
    char config_path[160];
    char client[32];
    int lan_scope;
#ifdef WA_TESTING
    int test_loopback,test_refresh_delay_ms,test_hold_first_ms;
    unsigned test_port_base;
#endif
} wa_config;

typedef struct {
    unsigned magic;
    double facts_started,ct_started,completed;
    wa_health health;
    wa_domain domain;
    wa_cidr lan;
    int weight[2];
    char affinity_seed[65];
    wa_ct_view view;
} wa_observation;
typedef struct {wa_engine *engine;const wa_config *config;} wa_collect_context;

static void config_defaults(wa_config *c) {
    memset(c, 0, sizeof *c);
    c->mode = WA_STICKY;
    c->queue_num = 20880;
    c->queue_maxlen = 1024;
    c->idle_seconds = 0.0;
    c->max_reconcile_seconds = 15.0;
    c->provisional_warning_seconds = 2.0;
    c->reconcile_interval = 2.0;
    snprintf(c->snapshot_path, sizeof c->snapshot_path, "/tmp/wan-watchdog/health.v1");
    snprintf(c->health_socket,sizeof c->health_socket,"/tmp/wan-watchdog/health.sock");
    c->health_source=WA_HEALTH_AUTO;
    snprintf(c->status_dir, sizeof c->status_dir, "/tmp/wan-affinity");
    snprintf(c->config_path, sizeof c->config_path, "/jffs/wan-affinity/wan-affinity.conf");
}

static char *trim(char *s) {
    while (*s == ' ' || *s == '\t') s++;
    char *e = s + strlen(s);
    while (e > s && (e[-1] == '\n' || e[-1] == ' ' || e[-1] == '\t')) *--e = 0;
    return s;
}

static double config_number(const char *key, const char *value, int integer) {
    char *end;
    errno = 0;
    double number = strtod(value, &end);
    if (errno || end == value || *end || !isfinite(number) ||
        (integer && (number < 0 || number > INT_MAX || number != (int)number))) {
        fprintf(stderr, "invalid numeric setting %s\n", key);
        exit(2);
    }
    return number;
}

static void config_load(wa_config *c, const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        char *s = trim(line);
        if (*s == 0 || *s == '#') continue;
        char *eq = strchr(s, '=');
        if (!eq) continue;
        *eq = 0;
        char *k = trim(s);
        char *v = trim(eq + 1);
        if (!strcmp(k, "MODE")) {
            if(strcmp(v,"sticky") && strcmp(v,"affinity")){fprintf(stderr,"invalid MODE\n");exit(2);}
            c->mode = !strcmp(v, "affinity") ? WA_AFFINITY : WA_STICKY;
        }
        else if (!strcmp(k, "CLIENT")) snprintf(c->client, sizeof c->client, "%s", v);
        else if (!strcmp(k, "LAN_SCOPE")) c->lan_scope = !strcmp(v, "1");
        else if (!strcmp(k, "QUEUE_NUM")) c->queue_num = (int)config_number(k,v,1);
        else if (!strcmp(k, "QUEUE_MAXLEN")) c->queue_maxlen = (int)config_number(k,v,1);
        else if (!strcmp(k, "IDLE_SECONDS")) c->idle_seconds = config_number(k,v,0);
        else if (!strcmp(k, "MAX_RECONCILE_SECONDS")) c->max_reconcile_seconds = config_number(k,v,0);
        else if (!strcmp(k, "PROVISIONAL_WARNING_SECONDS")) c->provisional_warning_seconds = config_number(k,v,0);
        else if (!strcmp(k, "RECONCILE_INTERVAL")) c->reconcile_interval = config_number(k,v,0);
        else if (!strcmp(k, "AFFINITY_SEED")) snprintf(c->affinity_seed, sizeof c->affinity_seed, "%s", v);
        else if (!strcmp(k, "SNAPSHOT_PATH")) snprintf(c->snapshot_path, sizeof c->snapshot_path, "%s", v);
        else if (!strcmp(k,"HEALTH_SOCKET")) snprintf(c->health_socket,sizeof c->health_socket,"%s",v);
        else if (!strcmp(k,"HEALTH_SOURCE")) {
            if(!strcmp(v,"auto"))c->health_source=WA_HEALTH_AUTO;
            else if(!strcmp(v,"firmware"))c->health_source=WA_HEALTH_FIRMWARE;
            else if(!strcmp(v,"watchdog"))c->health_source=WA_HEALTH_WATCHDOG;
            else if(!strcmp(v,"legacy"))c->health_source=WA_HEALTH_LEGACY;
            else {fprintf(stderr,"invalid HEALTH_SOURCE\n");exit(2);}
        }
        else if (!strcmp(k, "STATUS_DIR")) snprintf(c->status_dir, sizeof c->status_dir, "%s", v);
    }
    fclose(f);
}

/* ------------------------------------------------------------------ */
/* nvram / router facts                                               */
/* ------------------------------------------------------------------ */

static int nvram_get(const char *name, char *buf, size_t len) {
    char cmd[128];
    snprintf(cmd, sizeof cmd, "nvram get %s 2>/dev/null", name);
    FILE *p = popen(cmd, "r");
    if (!p) return 0;
    buf[0] = 0;
    if (!fgets(buf, (int)len, p)) buf[0] = 0;
    pclose(p);
    char *s = trim(buf);
    if (s != buf) memmove(buf, s, strlen(s) + 1);
    return buf[0] != 0;
}

static void domain_from_router(wa_engine *e, const wa_config *c) {
#ifdef WA_TESTING
    if(c->test_loopback) {
        memset(&e->domain,0,sizeof e->domain);
        wa_domain_set_lan(e,0x7f580002u,0xffffffffu);
        return;
    }
#endif
    e->domain.n_local = e->domain.n_vpn = e->domain.n_pins = 0;
    const char *reserved[] = {"0.0.0.0/8","10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","127.0.0.0/8","169.254.0.0/16","224.0.0.0/4","255.255.255.255/32"};
    for (unsigned i=0;i<sizeof reserved/sizeof reserved[0];i++) {
        uint32_t n,m; if(!wa_parse_cidr(reserved[i],&n,&m)) wa_domain_add_local(e,n,m);
    }
    char ip[32], mask[32];
    uint32_t addr = 0, m = 0xffffff00u;
    nvram_get("lan_ipaddr", ip, sizeof ip);
    nvram_get("lan_netmask", mask, sizeof mask);
    if (ip[0] && wa_parse_ipv4(ip, &addr) == 0) {
        if (mask[0]) wa_parse_ipv4(mask, &m);
        wa_domain_set_lan(e, addr & m, m);
        wa_domain_add_local(e,addr & m,m);
    } else {
        wa_domain_set_lan(e, 0xc0a80100u /* 192.168.1.0 */, 0xffffff00u);
    }
    /* GUI pins: <ip>all>unit ... - exclude them from automatic assignment */
    if (!c->lan_scope) {
        uint32_t client;
        if (wa_parse_ipv4(c->client,&client) || (client&m)!=(addr&m) || client==addr) {
            fprintf(stderr,"invalid single LAN client scope\n");exit(2);
        }
        wa_domain_set_lan(e,client,0xffffffffu);
    }
    char rl[8192];
    if (nvram_get("wans_routing_rulelist", rl, sizeof rl)) {
        char *tok = strtok(rl, "<");
        while (tok) {
            char *gt = strchr(tok, '>');
            if (gt) {
                *gt = 0;
                char *destination=gt+1,*end=strchr(destination,'>');
                if(end)*end=0;
                uint32_t s=0,sm=0,d=0,dm=0;
                int bad=0;
                if(*tok && strcmp(tok,"all"))bad=wa_parse_cidr(tok,&s,&sm);
                if(*destination && strcmp(destination,"all"))bad|=wa_parse_cidr(destination,&d,&dm);
                if(bad || e->domain.n_pins>=WA_MAX_PINS) {
                    fprintf(stderr,"unsupported or excessive GUI pins; refusing classification\n");exit(2);
                }
                wa_domain_add_pin(e,s,sm,d,dm);
            }
            tok = strtok(NULL, "<");
        }
    }
    FILE *routes=popen("ip -4 route show table main", "r");
    if(!routes)die("read local routes");
    char row[512];
    while(fgets(row,sizeof row,routes)) {
        char cidr[64];uint32_t n,maskv;
        if(sscanf(row,"%63s",cidr)!=1 || !strcmp(cidr,"default"))continue;
        if(!wa_parse_cidr(cidr,&n,&maskv) && maskv) {
            if(e->domain.n_local>=WA_MAX_CIDR*3){fprintf(stderr,"too many local routes\n");exit(2);}
            wa_domain_add_local(e,n,maskv);
        }
    }
    if(pclose(routes))die("local route read");
    /* Preserve policy routing outside firmware WAN selection. For unfamiliar
     * tables, exclude the complete source/destination selector even if the
     * rule also has a mark/interface condition. This can deliberately reduce
     * coverage, but cannot pull VPN traffic into a WAN table. */
    routes=popen("ip -4 rule show", "r");
    if(!routes)die("policy rule read");
    while(fgets(row,sizeof row,routes)) {
        uint32_t s=0,sm=0,d=0,dm=0;
        int excluded=wa_policy_exclusion(row,&s,&sm,&d,&dm);
        if(excluded<0){fprintf(stderr,"truncated policy rule\n");exit(2);}
        if(!excluded)continue;
        if(e->domain.n_pins>=WA_MAX_PINS){fprintf(stderr,"too many policy exclusions\n");exit(2);}
        wa_domain_add_pin(e,s,sm,d,dm);
    }
    if(pclose(routes))die("policy rule read");
}

static void firmware_health_from_router(wa_engine *e) {
    for (int u = 0; u < 2; u++) {
        char key[32], v[64];
        wa_wan_facts f;
        memset(&f, 0, sizeof f);
        snprintf(key, sizeof key, "wan%d_enable", u);
        f.enable = nvram_get(key, v, sizeof v) ? atoi(v) : -1;
        snprintf(key, sizeof key, "wan%d_state_t", u);
        f.state_t = nvram_get(key, v, sizeof v) ? atoi(v) : -1;
        snprintf(key, sizeof key, "wan%d_ipaddr", u);
        if (!nvram_get(key, f.ip, sizeof f.ip)) f.ip[0] = 0;
        snprintf(key, sizeof key, "wan%d_gateway", u);
        if (!nvram_get(key, f.gw, sizeof f.gw)) f.gw[0] = 0;
        snprintf(key, sizeof key, "wan%d_ifname", u);
        if (!nvram_get(key, f.ifname, sizeof f.ifname)) f.ifname[0] = 0;
        f.route_ready = 0;
        char cmd[96],row[256];
        snprintf(cmd,sizeof cmd,"ip -4 route show table wan%d default",u);
        FILE *routes=popen(cmd,"r");
        if(routes) {
            while(fgets(row,sizeof row,routes)) {
                char dev[32];snprintf(dev,sizeof dev,"dev %s",f.ifname);
                if(!strncmp(row,"default ",8) && strstr(row,dev))f.route_ready=1;
            }
            if(pclose(routes))f.route_ready=-1;
        } else f.route_ready=-1;
        wa_health_set_firmware(e, u, &f);
    }
}

static void ratio_from_router(wa_engine *e) {
    char r[32];
    if (nvram_get("wans_lb_ratio", r, sizeof r)) {
        int a = 1, b = 1;
        if (sscanf(r, "%d:%d", &a, &b) == 2 && a > 0 && b > 0) {
            e->weight[0] = a;
            e->weight[1] = b;
        }
    }
}

static void derive_affinity_seed(char *out, size_t len) {
    char mac[32] = "";
    nvram_get("et0macaddr", mac, sizeof mac);
    if (!mac[0]) nvram_get("wan0_hwaddr", mac, sizeof mac);
    uint64_t h = 1469598103934665603ULL;
    for (const char *p = mac; *p; p++) {
        h ^= (unsigned char)*p;
        h *= 1099511628211ULL;
    }
    snprintf(out, len, "%016llx", (unsigned long long)h);
}

/* ------------------------------------------------------------------ */
/* optional enhanced health snapshot                                  */
/* ------------------------------------------------------------------ */

static void load_snapshot(wa_engine *e, const wa_config *c, double now) {
    FILE *f = fopen(c->snapshot_path, "r");
    if (!f) {
        wa_health_apply_snapshot(e, NULL, now); /* no enhanced -> firmware; exclusions persist */
        return;
    }
    static char buf[4096];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    int err = ferror(f);
    fclose(f);
    if (err) return; /* keep the previous valid snapshot on a bad read */
    buf[n] = 0;
    char boot[80];FILE *bf=fopen("/proc/sys/kernel/random/boot_id","r");
    if(!bf)return;
    if(!fgets(boot,sizeof boot,bf)){fclose(bf);return;}fclose(bf);
    char expected[128];snprintf(expected,sizeof expected,"publisher_boot_id=%s",trim(boot));
    if(!strstr(buf,expected))return;
    wa_health_apply_snapshot(e, buf, now);
}

/* ------------------------------------------------------------------ */
/* netlink NFQUEUE                                                    */
/* ------------------------------------------------------------------ */

static void nla_put(struct nlmsghdr *h, unsigned type, const void *data, size_t len) {
    size_t pos = NLMSG_ALIGN(h->nlmsg_len);
    struct nlattr *a = (struct nlattr *)((char *)h + pos);
    a->nla_type = type;
    a->nla_len = NLA_HDRLEN + len;
    memcpy((char *)a + NLA_HDRLEN, data, len);
    memset((char *)a + a->nla_len, 0, NLA_ALIGN(a->nla_len) - a->nla_len);
    h->nlmsg_len = pos + NLA_ALIGN(a->nla_len);
}

static struct nlmsghdr *msg(char *buf, unsigned type, unsigned queue, int ack) {
    memset(buf, 0, 4096);
    struct nlmsghdr *h = (struct nlmsghdr *)buf;
    h->nlmsg_len = NLMSG_LENGTH(sizeof(struct nfgenmsg));
    h->nlmsg_type = (NFNL_SUBSYS_QUEUE << 8) | type;
    h->nlmsg_flags = NLM_F_REQUEST | (ack ? NLM_F_ACK : 0);
    h->nlmsg_seq = ++sequence;
    struct nfgenmsg *g = NLMSG_DATA(h);
    g->nfgen_family = AF_UNSPEC;
    g->version = NFNETLINK_V0;
    g->res_id = htons(queue);
    return h;
}

static void nl_send(int fd, struct nlmsghdr *h, int ack) {
    struct sockaddr_nl dst = {.nl_family = AF_NETLINK};
    if (sendto(fd, h, h->nlmsg_len, 0, (void *)&dst, sizeof dst) < 0) die("netlink send");
    if (!ack) return;
    uint32_t seq = h->nlmsg_seq;
    char buf[8192];
    for (;;) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, 2000) <= 0) {
            fprintf(stderr, "netlink ACK timeout\n");
            exit(1);
        }
        ssize_t got = recv(fd, buf, sizeof buf, 0);
        if (got < 0) die("netlink ACK recv");
        size_t offset=0;struct nlmsghdr *r;
        while ((r=(void*)wa_nl_frame(buf,(size_t)got,&offset))) {
            if (r->nlmsg_type == NLMSG_ERROR && r->nlmsg_seq == seq) {
                struct nlmsgerr *er = NLMSG_DATA(r);
                if (er->error) {
                    errno = -er->error;
                    die("netlink configuration");
                }
                return;
            }
        }
    }
}

static int bind_queue(const wa_config *c) {
    int fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_NETFILTER);
    if (fd < 0) die("NETLINK_NETFILTER socket");
    struct sockaddr_nl local = {.nl_family = AF_NETLINK};
    if (bind(fd, (void *)&local, sizeof local)) die("netlink bind");
    char buf[4096];
    struct nlmsghdr *h = msg(buf, NFQNL_MSG_CONFIG, (unsigned)c->queue_num, 1);
    struct nfqnl_msg_config_cmd cmd = {.command = NFQNL_CFG_CMD_BIND, .pf = htons(AF_INET)};
    nla_put(h, NFQA_CFG_CMD, &cmd, sizeof cmd);
    nl_send(fd, h, 1);

    h = msg(buf, NFQNL_MSG_CONFIG, (unsigned)c->queue_num, 1);
    struct nfqnl_msg_config_params params = {.copy_range = htonl(128),
                                             .copy_mode = NFQNL_COPY_PACKET};
    nla_put(h, NFQA_CFG_PARAMS, &params, sizeof params);
    nl_send(fd, h, 1);

    h = msg(buf, NFQNL_MSG_CONFIG, (unsigned)c->queue_num, 1);
    uint32_t maxlen = htonl((uint32_t)c->queue_maxlen);
    uint32_t flags = htonl(NFQA_CFG_F_FAIL_OPEN | NFQA_CFG_F_CONNTRACK);
    nla_put(h, NFQA_CFG_QUEUE_MAXLEN, &maxlen, sizeof maxlen);
    nla_put(h, NFQA_CFG_MASK, &flags, sizeof flags);
    nla_put(h, NFQA_CFG_FLAGS, &flags, sizeof flags);
    nl_send(fd, h, 1);

    int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    return fd;
}

static void verdict(int fd, const wa_config *c, uint32_t id, uint32_t mark, unsigned decision) {
    char buf[4096];
    struct nlmsghdr *h = msg(buf, NFQNL_MSG_VERDICT, (unsigned)c->queue_num, 0);
    struct nfqnl_msg_verdict_hdr v = {.verdict = htonl(decision), .id = htonl(id)};
    nla_put(h, NFQA_VERDICT_HDR, &v, sizeof v);
    uint32_t m = htonl(mark);
    nla_put(h, NFQA_MARK, &m, sizeof m);
    nl_send(fd, h, 0);
}

/* ------------------------------------------------------------------ */
/* conntrack parsing                                                  */
/* ------------------------------------------------------------------ */

/* Unconfirmed comes FIRST: U -> C migration between reads cannot hide a
 * still-live tuple. CLI filters are ignored by this firmware's U dump, so
 * filtering and deduplication happen explicitly in the worker. */
static int collect_table(wa_engine *e,const wa_config *c,wa_ct_view *view,int unconfirmed) {
    /* Keep stdout rows and stderr completion separately. Merging 2>&1 lets
     * stdio buffering splice the completion message into a partial row once a
     * dump exceeds one buffer. The anonymous stderr file lives in /tmp RAM. */
    FILE *errors=tmpfile();if(!errors)return -1;
    int fds[2];if(pipe(fds)){fclose(errors);return -1;}
    pid_t child=fork();
    if(child<0){close(fds[0]);close(fds[1]);fclose(errors);return -1;}
    if(!child) {
        dup2(fds[1],STDOUT_FILENO);dup2(fileno(errors),STDERR_FILENO);
        close(fds[0]);close(fds[1]);fclose(errors);
        char *argv[12];int n=0;
        argv[n++]="conntrack";argv[n++]="-L";
        if(unconfirmed)argv[n++]="unconfirmed";
        argv[n++]="-f";argv[n++]="ipv4";argv[n++]="-o";argv[n++]="extended,id";
        if(!unconfirmed && !c->lan_scope){argv[n++]="-s";argv[n++]=(char*)c->client;}
        argv[n]=NULL;execvp(argv[0],argv);_exit(127);
    }
    close(fds[1]);FILE *p=fdopen(fds[0],"r");
    if(!p){close(fds[0]);fclose(errors);kill(child,SIGKILL);waitpid(child,NULL,0);return -1;}
    char line[1024];int rows=0,summary=-1,bad=0;
    while(fgets(line,sizeof line,p)) {
        if(!strchr(line,'\n')){bad=1;continue;}
        unsigned family=2,proto=0;char name[32];
        if(!strncmp(line,"ipv4 ",5) || !strncmp(line,"ipv6 ",5)) {
            char label[16];
            if(sscanf(line,"%15s %u %31s %u",label,&family,name,&proto)!=4){bad=1;continue;}
        } else if(sscanf(line,"%31s %u",name,&proto)!=2){bad=1;continue;}
        if(proto>255){bad=1;continue;}
        rows++;
        const char *source=strstr(line,"src=");
        if(family!=2 || (source && memchr(source+4,':',strcspn(source+4," \t\r\n"))))continue;
        wa_ct_record row;
        int parsed=unconfirmed?wa_parse_unconfirmed_conntrack(line,&row):wa_parse_firmware_conntrack(line,&row);
        if(parsed<0 || ((proto==6 || proto==17) && !parsed)){bad=1;continue;}
        if(!parsed)continue;
        if((row.src&e->lan.mask)!=(e->lan.net&e->lan.mask))continue;
#ifdef WA_TESTING
        if(c->test_loopback && (row.dst!=0x7f580001u || row.proto!=17 || row.dport!=58888))continue;
        if(c->test_port_base && (row.sport<c->test_port_base || row.sport>=c->test_port_base+1000))continue;
#endif
        if(wa_ct_view_add(view,&row))bad=1;
    }
    if(ferror(p))bad=1;
    fclose(p);int status=-1;
    while(waitpid(child,&status,0)<0 && errno==EINTR){}
    if(fflush(errors) || fseek(errors,0,SEEK_END) || ftell(errors)>2048 || fseek(errors,0,SEEK_SET))bad=1;
    while(fgets(line,sizeof line,errors)) {
        const char *number=strstr(line,"): ");char *end=NULL;
        if(strncmp(line,"conntrack ",10) || !number || summary>=0){bad=1;continue;}
        long count=strtol(number+3,&end,10);
        if(end==number+3 || count<0 || count>1000000 ||
           strcmp(end," flow entries have been shown.\n")){bad=1;continue;}
        summary=(int)count;
    }
    if(ferror(errors))bad=1;
    fclose(errors);
    if(status || bad || summary!=rows) {
        fprintf(stderr,"connection view rejected table=%s status=%d bad=%d summary=%d rows=%d\n",unconfirmed?"U":"C",status,bad,summary,rows);
        return -1;
    }
    return 0;
}

static int collect_observation(void *arg,void *result,size_t capacity) {
    wa_collect_context *ctx=arg;wa_engine *e=ctx->engine;const wa_config *c=ctx->config;
    wa_observation *o=result;if(capacity!=sizeof *o)return -1;
    if(health_feed.client.fd>=0)close(health_feed.client.fd);
    memset(o,0,sizeof *o);o->facts_started=mono();
#ifdef WA_TESTING
    if(c->test_refresh_delay_ms && e->have_reconcile) {
        struct timespec delay={c->test_refresh_delay_ms/1000,(c->test_refresh_delay_ms%1000)*1000000L};
        while(nanosleep(&delay,&delay) && errno==EINTR){}
    }
#endif
    firmware_health_from_router(e);ratio_from_router(e);domain_from_router(e,c);
    if(e->mode==WA_AFFINITY && !e->affinity_seed[0])derive_affinity_seed(e->affinity_seed,sizeof e->affinity_seed);
    if(c->health_source==WA_HEALTH_LEGACY)load_snapshot(e,c,mono());
    o->health=e->health;o->domain=e->domain;o->lan=e->lan;
    memcpy(o->weight,e->weight,sizeof o->weight);
    memcpy(o->affinity_seed,e->affinity_seed,sizeof o->affinity_seed);
    o->ct_started=mono();
    if(collect_table(e,c,&o->view,1) || collect_table(e,c,&o->view,0))return -1;
    o->completed=mono();o->magic=0x57414632u;return 0;
}

static int apply_observation(wa_engine *e,const wa_observation *o,double now) {
    if(o->magic!=0x57414632u || o->view.count<0 || o->view.count>WA_MAX_STATES ||
       now-o->facts_started>e->max_reconcile_age || o->ct_started>o->completed || o->completed>now) {
        e->uncertain=1;snprintf(e->last_error,sizeof e->last_error,"stale-worker-result");return -1;
    }
    /* Enhanced socket state is parent-owned; an old worker cannot replace it. */
    if(health_feed.source==WA_HEALTH_LEGACY)e->health=o->health;
    else wa_health_merge_firmware(e,&o->health);
    wa_health_refresh(e,now);
    e->domain=o->domain;e->lan=o->lan;
    memcpy(e->weight,o->weight,sizeof e->weight);
    memcpy(e->affinity_seed,o->affinity_seed,sizeof e->affinity_seed);
    static wa_state states[WA_MAX_STATES];
    kernel_unconfirmed=0;
    for(int i=0;i<o->view.count;i++) {
        const wa_ct_record *row=&o->view.rows[i];wa_state *st=&states[i];memset(st,0,sizeof *st);
        st->ctid=wa_flow_token(e,row->proto,row->src,row->dst,row->sport,row->dport);
        st->kernel_id=row->kernel_id;
        st->src=row->src;st->dst=row->dst;st->sport=row->sport;st->dport=row->dport;st->proto=row->proto;
        st->wan=(int8_t)wa_ct_egress(row,&e->health);kernel_unconfirmed+=row->unconfirmed;
    }
    int rc=wa_reconcile_view(e,o->ct_started,now,states,o->view.count,1);
    if(!rc){wa_reconstruct(e);last_refresh_success=o->facts_started;refresh_ok++;}
    return rc;
}

/* ------------------------------------------------------------------ */
/* status                                                             */
/* ------------------------------------------------------------------ */

static void write_status(wa_engine *e, const wa_config *c) {
    char text[8192];
    wa_engine_status(e, text, sizeof text);
    char path[200], tmp[220];
    snprintf(path, sizeof path, "%s/status", c->status_dir);
    snprintf(tmp, sizeof tmp, "%s/status.new", c->status_dir);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fputs(text, f);
    char instance[33];long long evidence_age_ms[2];uint64_t health_now=wh_now();
    for(int i=0;i<16;i++)snprintf(instance+2*i,3,"%02x",health_feed.client.state.instance[i]);
    for(int u=0;u<2;u++) {
        uint64_t measured=health_feed.client.state.wan[u].measured_ns;
        evidence_age_ms[u]=measured && health_now>=measured?(long long)((health_now-measured)/1000000):-1;
    }
    fprintf(f,"health_source=%s socket_connected=%d socket_seq=%llu socket_rejected=%u socket_published_ns=%llu\n",
        c->health_source==WA_HEALTH_FIRMWARE?"firmware":c->health_source==WA_HEALTH_WATCHDOG?"watchdog":c->health_source==WA_HEALTH_LEGACY?"legacy":"auto",
        health_feed.client.connected,(unsigned long long)health_feed.client.state.seq,health_feed.client.rejected,(unsigned long long)health_feed.client.state.published_ns);
    fprintf(f,"socket_instance=%s evidence_age_ms=%lld/%lld publisher_reason=%s/%s publisher_excluded=%u/%u\n",instance,evidence_age_ms[0],evidence_age_ms[1],
        wh_reason_name(health_feed.client.state.wan[0].reason),wh_reason_name(health_feed.client.state.wan[1].reason),health_feed.client.state.wan[0].excluded,health_feed.client.state.wan[1].excluded);
    fprintf(f,"adapter loop_mono_ns=%llu queue_packets=%lu passes=%lu missing_ctid=%lu tcp=%lu udp=%lu lifecycle=unconfirmed+confirmed refresh_ok=%lu refresh_failed=%lu kernel_unconfirmed=%d refresh_pid=%ld refresh_ms=%.2f max_refresh_ms=%.2f max_timestamp_delay_us=%.0f\n",(unsigned long long)health_now,queue_packets,queue_passes,missing_ctid,tcp_admissions,udp_admissions,refresh_ok,refresh_failed,kernel_unconfirmed,(long)refresh_pid,last_refresh_ms,max_refresh_ms,max_timestamp_delay_us);
    fclose(f);
    if (rename(tmp, path) != 0) remove(tmp);
}

/* ------------------------------------------------------------------ */
/* main loop: no command execution or waiting for command children      */
/* ------------------------------------------------------------------ */

static uint64_t checkpoint_profile(const wa_engine *e,const wa_config *c) {
    char text[256];
    snprintf(text,sizeof text,"%d:%d:%s:%08x/%08x:%s",e->mode,c->lan_scope,c->client,
             e->lan.net,e->lan.mask,e->affinity_seed);
    uint64_t h=UINT64_C(1469598103934665603);
    for(const unsigned char *p=(const unsigned char *)text;*p;p++){h^=*p;h*=UINT64_C(1099511628211);}
    return h;
}

static void run(wa_engine *e, wa_config *c) {
    mkdir(c->status_dir, 0700);
    wa_health_feed_init(&health_feed,e,c->health_source,c->health_socket,c->status_dir);
    wa_worker job;wa_worker_init(&job);
    static wa_observation observation;
    wa_collect_context context={e,c};
    if(wa_worker_start(&job,&observation,sizeof observation,collect_observation,&context,-1,mono()))die("start initial worker");
    int initial=0;
    while(!stopping && !initial) {
        wa_health_feed_step(&health_feed,e,wh_now());
        struct pollfd p={job.fd,POLLIN,0};poll(&p,1,20);
        initial=wa_worker_poll(&job,&observation,mono(),5.0);
    }
    if(initial!=1 || apply_observation(e,&observation,mono())) {
        wa_worker_cancel(&job);fprintf(stderr,"initial full connection view invalid; refusing admission\n");return;
    }
    char mapping_cache[200];uint8_t mapping_boot[16];
    snprintf(mapping_cache,sizeof mapping_cache,"%s/mappings.cache",c->status_dir);
    int checkpoint_enabled=!wh_boot(mapping_boot);
    uint64_t profile=checkpoint_profile(e,c),saved_binding=e->next_binding_id,saved_epoch=e->epoch;
    if(checkpoint_enabled) {
        int restored=wa_checkpoint_load(mapping_cache,e,mapping_boot,profile,wh_now());
        fprintf(stderr,"source mapping checkpoint: %s\n",restored>0?"restored":restored<0?"rejected":"fresh start");
        wa_checkpoint_save(mapping_cache,e,mapping_boot,profile,wh_now());
    }
    int queue_fd=bind_queue(c);
    double next_reconcile=mono()+c->reconcile_interval,next_status=mono()+1;
    write_status(e,c);
    fprintf(stderr,"wan-affinity %s queue=%d mode=%s lifecycle=U-then-C async-refresh\n",WA_VERSION,c->queue_num,e->mode==WA_AFFINITY?"affinity":"sticky");
#ifdef WA_TESTING
    int held=0,held_once=0;uint32_t held_id=0,held_mark=0;double held_until=0;
#endif
    while(!stopping) {
        struct pollfd p[3]={{queue_fd,POLLIN,0},{job.fd,POLLIN,0},{health_feed.client.fd,POLLIN,0}};
        int rc=poll(p,3,20);
        if(rc<0){if(errno==EINTR)continue;die("poll");}
        double t=mono();
        wa_health_feed_step(&health_feed,e,wh_now());
        if(t-last_refresh_success>e->max_reconcile_age) {
            for(int u=0;u<2;u++)e->health.firmware[u].route_ready=-1;
            e->uncertain=1;snprintf(e->last_error,sizeof e->last_error,"stale-refresh");
        }
#ifdef WA_TESTING
        if(held && t>=held_until){verdict(queue_fd,c,held_id,held_mark,NF_REPEAT);held=0;}
#endif
        if (p[0].revents & POLLIN) {
            char buf[65536];
            ssize_t got = recv(queue_fd, buf, sizeof buf, 0);
            if (got > 0) {
                size_t offset=0;struct nlmsghdr *h;
                while ((h=(void*)wa_nl_frame(buf,(size_t)got,&offset))) {
                    if(h->nlmsg_len<NLMSG_LENGTH(sizeof(struct nfgenmsg)))continue;
                    if (h->nlmsg_type != ((NFNL_SUBSYS_QUEUE << 8) | NFQNL_MSG_PACKET)) continue;
                    queue_packets++;
                    uint32_t id = 0, mark = 0;
                    uint64_t ctid=0;
                    double timestamp=0;
                    unsigned char *payload = NULL;
                    size_t plen = 0;
                    int len = (int)(h->nlmsg_len - NLMSG_LENGTH(sizeof(struct nfgenmsg)));
                    struct nlattr *a =
                        (void *)((char *)NLMSG_DATA(h) + NLMSG_ALIGN(sizeof(struct nfgenmsg)));
                    while (len >= NLA_HDRLEN && a->nla_len >= NLA_HDRLEN && a->nla_len <= len) {
                        void *d = (char *)a + NLA_HDRLEN;
                        size_t an = a->nla_len - NLA_HDRLEN;
                        switch (a->nla_type & NLA_TYPE_MASK) {
                            case NFQA_PACKET_HDR:
                                if (an >= sizeof(struct nfqnl_msg_packet_hdr))
                                    id = ntohl(((struct nfqnl_msg_packet_hdr *)d)->packet_id);
                                break;
                            case NFQA_MARK:
                                if (an >= 4) {
                                    memcpy(&mark, d, 4);
                                    mark = ntohl(mark);
                                }
                                break;
                            case NFQA_PAYLOAD:
                                payload = d;
                                plen = an;
                                break;
                            case NFQA_TIMESTAMP:
                                if(an>=16) {
                                    uint32_t words[4];memcpy(words,d,16);
                                    double seconds=(double)(((uint64_t)ntohl(words[0])<<32)|ntohl(words[1]));
                                    double micros=(double)(((uint64_t)ntohl(words[2])<<32)|ntohl(words[3]));
                                    timestamp=seconds+micros/1e6;
                                }
                                break;
                            case NFQA_CT: {
                                int remain=(int)an;
                                struct nlattr *ct=d;
                                while(remain>=NLA_HDRLEN && ct->nla_len>=NLA_HDRLEN && ct->nla_len<=remain) {
                                    if((ct->nla_type&NLA_TYPE_MASK)==CTA_ID && ct->nla_len>=NLA_HDRLEN+4) {
                                        uint32_t val;memcpy(&val,(char*)ct+NLA_HDRLEN,4);ctid=ntohl(val);
                                    }
                                    int step=NLA_ALIGN(ct->nla_len);remain-=step;ct=(void*)((char*)ct+step);
                                }
                                break;
                            }
                            default:
                                break;
                        }
                        int step = NLA_ALIGN(a->nla_len);
                        len -= step;
                        a = (void *)((char *)a + step);
                    }
                    if(timestamp>0) {
                        struct timespec real;clock_gettime(CLOCK_REALTIME,&real);
                        double delta=((double)real.tv_sec+(double)real.tv_nsec/1e9-timestamp)*1e6;
                        if(delta>=0 && delta>max_timestamp_delay_us)max_timestamp_delay_us=delta;
                    }
                    if (!payload || plen < 20 || (payload[0] >> 4) != 4) {
                        queue_passes++;
                        verdict(queue_fd, c, id, (mark & WA_NONWAN_MASK) | WA_PASS_SENTINEL, NF_REPEAT);
                        continue;
                    }
                    uint8_t proto = payload[9];
                    uint32_t src, dst;
                    memcpy(&src, payload + 12, 4);
                    memcpy(&dst, payload + 16, 4);
                    src = ntohl(src);
                    dst = ntohl(dst);
                    unsigned ihl = (unsigned)(payload[0] & 15) * 4;
                    uint32_t sport = 0, dport = 0;
                    if (plen >= ihl + 4 && (proto == 6 || proto == 17)) {
                        sport = (uint32_t)((payload[ihl] << 8) | payload[ihl + 1]);
                        dport = (uint32_t)((payload[ihl + 2] << 8) | payload[ihl + 3]);
                    }
                    if(!ctid && (proto==6 || proto==17))missing_ctid++;
                    ctid=wa_flow_token(e,proto,src,dst,sport,dport);
                    /* Stamp the admission itself, not the earlier poll. A
                     * worker can begin its view while this batch is parsed. */
                    wa_admission r = wa_admission_decision(e, mono(), src, dst, proto, sport, dport,
                                                           mark, ctid);
                    if (r.action == WA_ACT_MARK) {
                        if(proto==6)tcp_admissions++;else if(proto==17)udp_admissions++;
#ifdef WA_TESTING
                        if(c->test_hold_first_ms && !held_once) {
                            held=held_once=1;held_id=id;held_mark=r.out_mark;
                            held_until=mono()+c->test_hold_first_ms/1000.0;
                        } else
#endif
                        verdict(queue_fd, c, id, r.out_mark, NF_REPEAT);
                    } else {
                        queue_passes++;
                        verdict(queue_fd, c, id, (mark & WA_NONWAN_MASK) | WA_PASS_SENTINEL, NF_REPEAT);
                    }
                }
            }
        }

        if(job.pid) {
            int ready=wa_worker_poll(&job,&observation,mono(),5.0);
            refresh_pid=job.pid;
            if(ready) {
                if(ready<0 || apply_observation(e,&observation,mono())) {
                    if(ready<0)fprintf(stderr,"refresh worker failed wait_status=%d\n",job.exit_status);
                    refresh_failed++;e->uncertain=1;
                    snprintf(e->last_error,sizeof e->last_error,"refresh-failed");
                } else {
                    last_refresh_ms=(observation.completed-observation.facts_started)*1000;
                    if(last_refresh_ms>max_refresh_ms)max_refresh_ms=last_refresh_ms;
                    wa_tick(e,mono());
                    if(checkpoint_enabled)wa_checkpoint_save(mapping_cache,e,mapping_boot,profile,wh_now());
                }
                write_status(e,c);
            }
        }
        if(!job.pid && t>=next_reconcile) {
            if(wa_worker_start(&job,&observation,sizeof observation,collect_observation,&context,queue_fd,mono())) {
                refresh_failed++;e->uncertain=1;
                snprintf(e->last_error,sizeof e->last_error,"worker-start-failed");
            }
            next_reconcile=t+c->reconcile_interval;
            refresh_pid=job.pid;
        }
        if(checkpoint_enabled && (saved_binding!=e->next_binding_id || saved_epoch!=e->epoch)) {
            if(!wa_checkpoint_save(mapping_cache,e,mapping_boot,profile,wh_now())) {
                saved_binding=e->next_binding_id;saved_epoch=e->epoch;
            }
        }
        if(t>=next_status){wa_tick(e,t);write_status(e,c);next_status=t+1;}
    }
    wa_worker_cancel(&job);
    /* Never wait in the admission path. Shutdown gives a killed child a small,
     * bounded reap window; queue closure cannot be delayed by a stuck command. */
    for(int n=0;job.pid && n<25;n++) {
        wa_worker_poll(&job,&observation,mono(),0);
        struct timespec brief={0,10000000L};nanosleep(&brief,NULL);
    }
    close(queue_fd);write_status(e,c);
    if(checkpoint_enabled)wa_checkpoint_save(mapping_cache,e,mapping_boot,profile,wh_now());
    wh_client_close(&health_feed.client,wh_now());
}

int main(int argc, char **argv) {
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    signal(SIGPIPE, SIG_IGN);

    wa_config c;
    config_defaults(&c);
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--config") && i + 1 < argc)
            snprintf(c.config_path, sizeof c.config_path, "%s", argv[i + 1]);
    config_load(&c, c.config_path);

    const char *mode_arg = NULL;
    int check_config = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--config") && i + 1 < argc) i++;
        else if (!strcmp(argv[i], "--queue") && i + 1 < argc) c.queue_num = (int)config_number("--queue",argv[++i],1);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) mode_arg = argv[++i];
        else if (!strcmp(argv[i], "--status-dir") && i + 1 < argc) snprintf(c.status_dir, sizeof c.status_dir, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--check-config")) check_config = 1;
#ifdef WA_TESTING
        else if(!strcmp(argv[i],"--loopback-test"))c.test_loopback=1;
        else if(!strcmp(argv[i],"--test-refresh-delay-ms") && i+1<argc)c.test_refresh_delay_ms=atoi(argv[++i]);
        else if(!strcmp(argv[i],"--test-hold-first-ms") && i+1<argc)c.test_hold_first_ms=atoi(argv[++i]);
        else if(!strcmp(argv[i],"--test-port-base") && i+1<argc)c.test_port_base=(unsigned)strtoul(argv[++i],NULL,10);
#endif
        else if (!strcmp(argv[i], "--version")) { printf("%s\n", WA_VERSION); return 0; }
        else { fprintf(stderr, "usage: %s [--config PATH] [--queue N] [--mode sticky|affinity] [--status-dir DIR]\n", argv[0]); return 2; }
    }
    if (mode_arg) {
        if(strcmp(mode_arg,"sticky") && strcmp(mode_arg,"affinity"))return 2;
        c.mode = !strcmp(mode_arg, "affinity") ? WA_AFFINITY : WA_STICKY;
    }

    static wa_engine engine;
    unsigned long long seed=0;
    FILE *random_source=fopen("/dev/urandom","rb");
    if(random_source){if(fread(&seed,sizeof seed,1,random_source)!=1)seed=0;fclose(random_source);}
    wa_engine_init(&engine, c.mode, 2, 1, c.idle_seconds, c.max_reconcile_seconds,
                   c.provisional_warning_seconds, c.affinity_seed, seed);
    if(!c.lan_scope) {
        uint32_t checked_ip;
        if(wa_parse_ipv4(c.client,&checked_ip)){fprintf(stderr,"single client required\n");return 2;}
    }
#ifdef WA_TESTING
    if(c.test_loopback && (strcmp(c.client,"127.88.0.2") || c.lan_scope)){fprintf(stderr,"fixed loopback scope required\n");return 2;}
    if(c.test_port_base && (c.lan_scope || c.test_loopback || (c.test_port_base!=41000 && c.test_port_base!=42000 && c.test_port_base!=43000 && c.test_port_base!=44000)))return 2;
    if(c.test_refresh_delay_ms<0 || c.test_refresh_delay_ms>10000 || c.test_hold_first_ms<0 || c.test_hold_first_ms>10000)return 2;
#endif
    if(c.queue_num<0 || c.queue_num>65535 || c.queue_maxlen<1 || c.queue_maxlen>4096 ||
       c.reconcile_interval<0.1 || c.reconcile_interval>60 || c.max_reconcile_seconds<1 ||
       c.max_reconcile_seconds>300 || c.idle_seconds<0 || c.provisional_warning_seconds<0)return 2;
    if(check_config){puts("configuration valid");return 0;}
    run(&engine, &c);
    return 0;
}
