#define _DARWIN_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "wa_checkpoint.h"
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAGIC UINT64_C(0x57414d4150303031)
#define MAX_AGE UINT64_C(300000000000)
typedef struct { uint32_t src; int32_t wan, preferred, failovers; uint64_t id, epoch; } binding;
typedef struct {
    uint64_t magic, profile, saved_ns, next_id, checksum;
    uint8_t boot[16];
    uint32_t version, state_size, assoc_count, state_count;
} header;
typedef struct { header h; binding a[WA_MAX_ASSOC]; wa_state s[WA_MAX_STATES]; } checkpoint;
static uint64_t hash(uint64_t h, const void *p, size_t n) {
    const unsigned char *b=p;
    for(size_t i=0;i<n;i++){h^=b[i];h*=UINT64_C(1099511628211);}
    return h;
}
static uint64_t checksum(checkpoint *c) {
    uint64_t saved=c->h.checksum;c->h.checksum=0;
    uint64_t h=hash(UINT64_C(1469598103934665603),&c->h,sizeof c->h);
    h=hash(h,c->a,c->h.assoc_count*sizeof c->a[0]);
    h=hash(h,c->s,c->h.state_count*sizeof c->s[0]);
    c->h.checksum=saved;return h;
}
static int same(const wa_state *a,const wa_state *b) {
    return a->src==b->src && a->dst==b->dst && a->proto==b->proto &&
        a->sport==b->sport && a->dport==b->dport && a->wan==b->wan &&
        (!a->kernel_id || a->kernel_id==b->kernel_id);
}
int wa_checkpoint_save(const char *path,const wa_engine *e,const uint8_t boot[16],
                       uint64_t profile,uint64_t now_ns) {
    if(e->uncertain || !e->have_reconcile)return -1;
    checkpoint *c=calloc(1,sizeof *c);if(!c)return -1;
    c->h.magic=MAGIC;c->h.version=1;c->h.state_size=sizeof(wa_state);
    c->h.profile=profile;c->h.saved_ns=now_ns;c->h.next_id=e->next_binding_id;
    memcpy(c->h.boot,boot,16);
    for(int i=0;i<e->n_state;i++)if(wa_state_relevant(e,&e->state[i]))
        c->s[c->h.state_count++]=e->state[i];
    for(int i=0;i<e->n_assoc;i++) {
        const wa_assoc *a=&e->assoc[i];int members=0;
        for(unsigned j=0;j<c->h.state_count;j++)members+=c->s[j].binding_id==a->binding_id;
        if(!members)continue;
        binding *b=&c->a[c->h.assoc_count++];
        b->src=a->src;b->wan=a->wan;b->preferred=a->preferred;b->failovers=a->failovers;
        b->id=a->binding_id;b->epoch=a->epoch;
    }
    c->h.checksum=checksum(c);
    char temp[256];int rc=-1;
    if(snprintf(temp,sizeof temp,"%s.new",path)>=(int)sizeof temp)goto done;
    int fd=open(temp,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,0600);
    if(fd<0)goto done;
    if(fchmod(fd,0600)){close(fd);goto done;}
    FILE *f=fdopen(fd,"wb");if(!f){close(fd);goto done;}
    int ok=fwrite(&c->h,sizeof c->h,1,f)==1 &&
        fwrite(c->a,sizeof c->a[0],c->h.assoc_count,f)==c->h.assoc_count &&
        fwrite(c->s,sizeof c->s[0],c->h.state_count,f)==c->h.state_count;
    if(fclose(f))ok=0;
    if(ok && !rename(temp,path))rc=0;
    if(rc)unlink(temp);
 done:free(c);return rc;
}
int wa_checkpoint_load(const char *path,wa_engine *e,const uint8_t boot[16],
                       uint64_t profile,uint64_t now_ns) {
    int fd=open(path,O_RDONLY|O_NOFOLLOW);if(fd<0)return 0;
    struct stat st;int rc=-1;
    if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || (st.st_mode&077)){
        close(fd);return -1;
    }
    FILE *f=fdopen(fd,"rb");if(!f){close(fd);return -1;}
    checkpoint *c=calloc(1,sizeof *c);if(!c){fclose(f);return -1;}
    if(fread(&c->h,sizeof c->h,1,f)!=1)goto done;
    if(c->h.magic!=MAGIC || c->h.version!=1 || c->h.state_size!=sizeof(wa_state) ||
       c->h.assoc_count>WA_MAX_ASSOC || c->h.state_count>WA_MAX_STATES ||
       c->h.next_id>=WA_BINDING_LEGACY-1)goto done;
    if(c->h.profile!=profile || memcmp(c->h.boot,boot,16) || c->h.saved_ns>now_ns ||
       now_ns-c->h.saved_ns>MAX_AGE){rc=0;goto done;}
    if(fread(c->a,sizeof c->a[0],c->h.assoc_count,f)!=c->h.assoc_count ||
       fread(c->s,sizeof c->s[0],c->h.state_count,f)!=c->h.state_count ||
       fgetc(f)!=EOF || ferror(f) || c->h.checksum!=checksum(c))goto done;
    for(unsigned i=0;i<c->h.assoc_count;i++) {
        binding *a=&c->a[i];
        if(!a->id || a->id>c->h.next_id || a->wan<0 || a->wan>1 ||
           a->preferred< -1 || a->preferred>1 || a->failovers<0)goto done;
        for(unsigned j=0;j<i;j++)if(c->a[j].id==a->id || c->a[j].src==a->src)goto done;
    }
    for(unsigned i=0;i<c->h.state_count;i++) {
        const wa_state *s=&c->s[i];
        if((s->proto!=6 && s->proto!=17) || s->wan< -1 || s->wan>1 ||
           s->sport>65535 || s->dport>65535 || s->dir_reply>1 || s->provisional>1)goto done;
        if(s->binding_id && s->binding_id!=WA_BINDING_LEGACY) {
            int found=0;
            for(unsigned j=0;j<c->h.assoc_count;j++)
                found|=s->src==c->a[j].src && s->binding_id==c->a[j].id;
            if(!found)goto done;
        }
    }
    /* Validate everything before touching the fresh observation. Cached tuples
     * never create states: both tuple and confirmed kernel identity must survive. */
    e->n_assoc=e->n_transitional=0;
    if(e->next_binding_id<c->h.next_id)e->next_binding_id=c->h.next_id;
    for(int i=0;i<e->n_state;i++) {
        wa_state *s=&e->state[i];s->binding_id=0;
        if(!wa_state_relevant(e,s))continue;
        for(unsigned j=0;j<c->h.state_count;j++)if(same(&c->s[j],s)){
            s->binding_id=c->s[j].binding_id;break;
        }
    }
    for(unsigned i=0;i<c->h.assoc_count;i++) {
        binding *b=&c->a[i];int members=0;
        for(int j=0;j<e->n_state;j++)members+=e->state[j].binding_id==b->id;
        if(!members)continue;
        wa_assoc *a=&e->assoc[e->n_assoc++];memset(a,0,sizeof *a);
        a->src=b->src;a->wan=b->wan;a->preferred=b->preferred;a->binding_id=b->id;
        a->epoch=b->epoch;a->failovers=b->failovers;a->adopted=1;a->reason="restored";
        a->created=e->last_reconcile_mono;
        if(e->epoch<a->epoch)e->epoch=a->epoch;
    }
    wa_reconstruct(e);rc=1;
 done:free(c);fclose(f);return rc;
}
