/* Test-only raw-netlink NFQUEUE bridge. No installer, routes or firewall writes.
 * The accompanying harness restricts it to loopback test packets. This is not
 * the production router implementation of the Python association engine.
 */
#define _GNU_SOURCE
#include "../src/wa_nl_frame.h"
#include <arpa/inet.h>
#include <errno.h>
#include <linux/netfilter.h>
#include <linux/netfilter/nfnetlink.h>
#include <linux/netfilter/nfnetlink_queue.h>
#include <linux/netlink.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopping;
static uint32_t sequence;
static void stop(int sig) { (void)sig; stopping = 1; }
static void die(const char *what) { perror(what); exit(1); }
static double mono(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void attr(struct nlmsghdr *h, unsigned type, const void *data, size_t len) {
    size_t pos = NLMSG_ALIGN(h->nlmsg_len);
    struct nlattr *a = (struct nlattr *)((char *)h + pos);
    a->nla_type = type; a->nla_len = NLA_HDRLEN + len;
    memcpy((char *)a + NLA_HDRLEN, data, len);
    memset((char *)a + a->nla_len, 0, NLA_ALIGN(a->nla_len)-a->nla_len);
    h->nlmsg_len = pos + NLA_ALIGN(a->nla_len);
}
static struct nlmsghdr *message(char *buf, unsigned type, unsigned queue, int ack) {
    memset(buf, 0, 4096);
    struct nlmsghdr *h = (struct nlmsghdr *)buf;
    h->nlmsg_len = NLMSG_LENGTH(sizeof(struct nfgenmsg));
    h->nlmsg_type = (NFNL_SUBSYS_QUEUE << 8) | type;
    h->nlmsg_flags = NLM_F_REQUEST | (ack ? NLM_F_ACK : 0);
    h->nlmsg_seq = ++sequence;
    struct nfgenmsg *g = NLMSG_DATA(h);
    g->nfgen_family = AF_UNSPEC; g->version = NFNETLINK_V0;
    g->res_id = htons(queue);
    return h;
}
static void send_message(int fd, struct nlmsghdr *h, int ack) {
    struct sockaddr_nl dst = {.nl_family = AF_NETLINK};
    if (sendto(fd, h, h->nlmsg_len, 0, (void *)&dst, sizeof dst) < 0) die("netlink send");
    if (!ack) return;
    uint32_t seq = h->nlmsg_seq;
    char buf[8192];
    for (;;) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, 2000) <= 0) { fprintf(stderr,"netlink ACK timeout\n"); exit(1); }
        ssize_t got = recv(fd, buf, sizeof buf, 0);
        if (got < 0) die("netlink ACK recv");
        size_t offset=0;struct nlmsghdr *r;
        while((r=(void *)wa_nl_frame(buf,(size_t)got,&offset))) {
            if (r->nlmsg_type == NLMSG_ERROR && r->nlmsg_seq == seq && r->nlmsg_len>=NLMSG_LENGTH(sizeof(struct nlmsgerr))) {
                struct nlmsgerr *e = NLMSG_DATA(r);
                if (e->error) { errno = -e->error; die("netlink configuration"); }
                return;
            }
        }
    }
}
static int bind_queue(unsigned queue, unsigned maxlen) {
    int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER);
    if (fd < 0) die("NETLINK_NETFILTER socket");
    struct sockaddr_nl local = {.nl_family = AF_NETLINK};
    if (bind(fd, (void *)&local, sizeof local)) die("netlink bind");
    char buf[4096];
    struct nlmsghdr *h = message(buf, NFQNL_MSG_CONFIG, queue, 1);
    struct nfqnl_msg_config_cmd cmd = {.command = NFQNL_CFG_CMD_BIND, .pf = htons(AF_INET)};
    attr(h, NFQA_CFG_CMD, &cmd, sizeof cmd); send_message(fd,h,1);
    h = message(buf, NFQNL_MSG_CONFIG, queue, 1);
    struct nfqnl_msg_config_params params = {.copy_range=htonl(2048), .copy_mode=NFQNL_COPY_PACKET};
    attr(h,NFQA_CFG_PARAMS,&params,sizeof params); send_message(fd,h,1);
    h = message(buf,NFQNL_MSG_CONFIG,queue,1);
    uint32_t m = htonl(maxlen), flags = htonl(NFQA_CFG_F_FAIL_OPEN);
    attr(h,NFQA_CFG_QUEUE_MAXLEN,&m,sizeof m);
    attr(h,NFQA_CFG_MASK,&flags,sizeof flags);
    attr(h,NFQA_CFG_FLAGS,&flags,sizeof flags); send_message(fd,h,1);
    return fd;
}
static void verdict(int fd, unsigned queue, uint32_t id, uint32_t mark, unsigned decision) {
    char buf[4096]; struct nlmsghdr *h = message(buf,NFQNL_MSG_VERDICT,queue,0);
    struct nfqnl_msg_verdict_hdr v = {.verdict=htonl(decision), .id=htonl(id)};
    attr(h,NFQA_VERDICT_HDR,&v,sizeof v); mark=htonl(mark);
    attr(h,NFQA_MARK,&mark,sizeof mark); send_message(fd,h,0);
}
static void packet(struct nlmsghdr *h) {
    uint32_t id=0, mark=0; unsigned char *payload=NULL; size_t plen=0;
    int len=h->nlmsg_len-NLMSG_LENGTH(sizeof(struct nfgenmsg));
    struct nlattr *a=(void *)((char *)NLMSG_DATA(h)+NLMSG_ALIGN(sizeof(struct nfgenmsg)));
    while (len>=NLA_HDRLEN && a->nla_len>=NLA_HDRLEN && a->nla_len<=len) {
        void *d=(char *)a+NLA_HDRLEN; size_t n=a->nla_len-NLA_HDRLEN;
        switch(a->nla_type & NLA_TYPE_MASK) {
        case NFQA_PACKET_HDR:
            if(n>=sizeof(struct nfqnl_msg_packet_hdr))
                id=ntohl(((struct nfqnl_msg_packet_hdr *)d)->packet_id);
            break;
        case NFQA_MARK: if(n>=4) {memcpy(&mark,d,4);mark=ntohl(mark);} break;
        case NFQA_PAYLOAD: payload=d; plen=n; break;
        }
        int step=NLA_ALIGN(a->nla_len); len-=step; a=(void *)((char *)a+step);
    }
    if(!payload || plen<20 || (payload[0]>>4)!=4) {
        fprintf(stderr,"unsupported test packet id=%u\n",id); stopping=1; return;
    }
    char src[INET_ADDRSTRLEN],dst[INET_ADDRSTRLEN];
    inet_ntop(AF_INET,payload+12,src,sizeof src); inet_ntop(AF_INET,payload+16,dst,sizeof dst);
    unsigned ihl=(payload[0]&15)*4, sport=0,dport=0;
    if(plen>=ihl+4 && (payload[9]==6 || payload[9]==17)) {
        sport=(payload[ihl]<<8)|payload[ihl+1]; dport=(payload[ihl+2]<<8)|payload[ihl+3];
    }
    printf("P %u %08x %s %s %u %u %u\n",id,mark,src,dst,payload[9],sport,dport); fflush(stdout);
}
static void bridge(unsigned queue, unsigned seconds, unsigned maxlen, int probe) {
    int fd=bind_queue(queue,maxlen);
    printf("READY queue=%u fail_open=1 maxlen=%u\n",queue,maxlen); fflush(stdout);
    if(probe) {close(fd);return;}
    double end=mono()+seconds; char lines[4096]; size_t used=0;
    while(!stopping && mono()<end) {
        struct pollfd p[2]={{fd,POLLIN,0},{STDIN_FILENO,POLLIN,0}};
        int rc=poll(p,2,100);
        if(rc<0) {if(errno==EINTR)continue;die("poll");}
        if(p[0].revents&POLLIN) {
            char buf[65536]; ssize_t got=recv(fd,buf,sizeof buf,0);
            if(got<0)die("packet recv");
            size_t offset=0;struct nlmsghdr *h;
            while((h=(void *)wa_nl_frame(buf,(size_t)got,&offset))) {
                if(h->nlmsg_len<NLMSG_LENGTH(sizeof(struct nfgenmsg)))continue;
                if(h->nlmsg_type==((NFNL_SUBSYS_QUEUE<<8)|NFQNL_MSG_PACKET)) packet(h);
                else if(h->nlmsg_type==NLMSG_ERROR && h->nlmsg_len>=NLMSG_LENGTH(sizeof(struct nlmsgerr))) {
                    struct nlmsgerr *e=NLMSG_DATA(h);
                    if(e->error) {errno=-e->error;die("verdict ACK");}
                }
            }
        }
        if(p[1].revents&(POLLIN|POLLHUP)) {
            ssize_t n=read(0,lines+used,sizeof(lines)-used-1);
            if(n<=0)break;
            used+=n;lines[used]=0;char *start=lines,*nl;
            while((nl=strchr(start,'\n'))) {
                *nl=0;unsigned id,mark;
                if(sscanf(start,"V %u %x",&id,&mark)==2)verdict(fd,queue,id,mark,NF_ACCEPT);
                else if(sscanf(start,"R %u %x",&id,&mark)==2)verdict(fd,queue,id,mark,NF_REPEAT);
                else if(!strcmp(start,"STOP"))stopping=1;
                else {fprintf(stderr,"bad bridge command\n");stopping=1;}
                start=nl+1;
            }
            used-=start-lines;memmove(lines,start,used);
            if(used==sizeof(lines)-1){fprintf(stderr,"command too long\n");break;}
        }
    }
    close(fd);
}
static int udp_socket(const char *ip,unsigned port) {
    int fd=socket(AF_INET,SOCK_DGRAM,0);if(fd<0)die("udp socket");
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(port)};
    if(inet_pton(AF_INET,ip,&a.sin_addr)!=1)die("address");
    if(bind(fd,(void *)&a,sizeof a))die("udp bind");return fd;
}
static void sink(unsigned seconds,unsigned port) {
    int fd=udp_socket("127.88.0.1",port);double end=mono()+seconds;unsigned total=0;
    printf("SINK_READY\n");fflush(stdout);
    while(!stopping && mono()<end) {
        struct pollfd p={fd,POLLIN,0};if(poll(&p,1,100)<=0)continue;
        char buf[128];struct sockaddr_in peer;socklen_t n=sizeof peer;
        ssize_t got=recvfrom(fd,buf,sizeof buf,0,(void *)&peer,&n);
        if(got>=0){total++;sendto(fd,"R",1,0,(void *)&peer,n);}
    }
    printf("SINK_TOTAL %u\n",total);close(fd);
}
static void send_udp(unsigned count,unsigned base,unsigned port) {
    struct sockaddr_in dst={.sin_family=AF_INET,.sin_port=htons(port)};
    inet_pton(AF_INET,"127.88.0.1",&dst.sin_addr);
    for(unsigned i=0;i<count;i++) {
        int fd=udp_socket("127.88.0.2",base+i);
        if(sendto(fd,"WA_TEST",7,0,(void *)&dst,sizeof dst)!=7)die("udp send");
        close(fd);
    }
    printf("SENT %u\n",count);
}
static void parallel_udp(unsigned workers,unsigned count,unsigned base,unsigned port) {
    if(!workers || workers>8 || !count || count>128 || base+workers*count>65535) {
        fprintf(stderr,"invalid bounded parallel test\n");exit(2);
    }
    int gate[2];if(pipe(gate))die("pipe");
    for(unsigned i=0;i<workers;i++) {
        pid_t child=fork();if(child<0)die("fork");
        if(!child) {
            close(gate[1]);char go;
            if(read(gate[0],&go,1)!=1)_exit(2);
            close(gate[0]);send_udp(count,base+i*count,port);exit(0);
        }
    }
    close(gate[0]);
    for(unsigned i=0;i<workers;i++)if(write(gate[1],"G",1)!=1)die("gate write");
    close(gate[1]);int status;unsigned failed=0;
    while(wait(&status)>0)if(!WIFEXITED(status)||WEXITSTATUS(status))failed++;
    if(failed){fprintf(stderr,"parallel child failures=%u\n",failed);exit(1);}
}
static int compare_double(const void *a,const void *b) {
    double x=*(const double*)a,y=*(const double*)b;return (x>y)-(x<y);
}
static void roundtrip_udp(unsigned count,unsigned base,unsigned port) {
    if(!count || count>128 || base+count>65535)exit(2);
    struct sockaddr_in dst={.sin_family=AF_INET,.sin_port=htons(port)};
    inet_pton(AF_INET,"127.88.0.1",&dst.sin_addr);
    double times[128],total=0;
    for(unsigned i=0;i<count;i++) {
        int fd=udp_socket("127.88.0.2",base+i);double start=mono();
        if(sendto(fd,"WA_TEST",7,0,(void*)&dst,sizeof dst)!=7)die("roundtrip send");
        struct pollfd p={fd,POLLIN,0};
        if(poll(&p,1,2500)!=1){fprintf(stderr,"roundtrip timeout\n");exit(2);}
        char reply;if(recv(fd,&reply,1,0)!=1 || reply!='R')exit(2);
        times[i]=(mono()-start)*1000;total+=times[i];close(fd);
    }
    qsort(times,count,sizeof times[0],compare_double);
    printf("{\"count\":%u,\"average_ms\":%.3f,\"p95_ms\":%.3f,\"max_ms\":%.3f}\n",
           count,total/count,times[(count*95+99)/100-1],times[count-1]);
}
/* One confirmed socket keeps sending while a different admission is frozen. */
static void steady_udp(unsigned seconds,unsigned base,unsigned port) {
    if(seconds>600 || base>65535 || !seconds)exit(2);
    int fd=udp_socket("127.88.0.2",base);
    struct sockaddr_in dst={.sin_family=AF_INET,.sin_port=htons(port)};
    inet_pton(AF_INET,"127.88.0.1",&dst.sin_addr);
    double end=mono()+seconds,max_ms=0;unsigned sent=0,lost=0;
    while(!stopping && mono()<end) {
        double start=mono();
        if(sendto(fd,"WA_TEST",7,0,(void *)&dst,sizeof dst)!=7)die("steady send");
        sent++;struct pollfd p={fd,POLLIN,0};
        int ready=poll(&p,1,200);
        if(ready<0 && stopping)break;
        if(ready!=1)lost++;
        else {char b;recv(fd,&b,1,0);double ms=(mono()-start)*1000;if(ms>max_ms)max_ms=ms;}
        struct timespec pause={0,50000000};nanosleep(&pause,NULL);
    }
    printf("{\"sent\":%u,\"lost\":%u,\"max_ms\":%.3f}\n",sent,lost,max_ms);close(fd);
}
int main(int argc,char **argv) {
    signal(SIGTERM,stop);signal(SIGINT,stop);signal(SIGPIPE,SIG_IGN);
    if(argc==3 && !strcmp(argv[1],"fifo")) {
        const char *suffix="/run/mappings.cache.new";size_t n=strlen(argv[2]),m=strlen(suffix);
        if(strncmp(argv[2],"/tmp/wa-stall-",14) || strstr(argv[2],"..") || n<m || strcmp(argv[2]+n-m,suffix))return 2;
        if(mkfifo(argv[2],0600))die("test fifo");
    }
    else if(argc==3 && !strcmp(argv[1],"probe"))bridge(atoi(argv[2]),2,16,1);
    else if(argc==5 && !strcmp(argv[1],"bridge"))bridge(atoi(argv[2]),atoi(argv[3]),atoi(argv[4]),0);
    else if(argc==4 && !strcmp(argv[1],"sink"))sink(atoi(argv[2]),atoi(argv[3]));
    else if(argc==5 && !strcmp(argv[1],"send"))send_udp(atoi(argv[2]),atoi(argv[3]),atoi(argv[4]));
    else if(argc==6 && !strcmp(argv[1],"parallel"))parallel_udp(atoi(argv[2]),atoi(argv[3]),atoi(argv[4]),atoi(argv[5]));
    else if(argc==5 && !strcmp(argv[1],"steady"))steady_udp(atoi(argv[2]),atoi(argv[3]),atoi(argv[4]));
    else if(argc==5 && !strcmp(argv[1],"roundtrip"))roundtrip_udp(atoi(argv[2]),atoi(argv[3]),atoi(argv[4]));
    else {fprintf(stderr,"probe QUEUE | bridge QUEUE SECONDS MAXLEN | sink SECONDS PORT | send COUNT BASEPORT PORT | parallel WORKERS COUNT BASEPORT PORT\n");return 2;}
    return 0;
}
