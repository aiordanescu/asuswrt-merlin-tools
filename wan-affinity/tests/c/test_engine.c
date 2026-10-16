/* Host tests for the pure-C decision engine. Build with the system cc:
 *   cc -std=c11 -Wall -Wextra -Werror -I../../src test_engine.c ../../src/wa_engine.c -o test_engine
 * These exercise the model contract; they do not touch a router.
 */
#include "wa_engine.h"
#include "wa_conntrack.h"
#include "wa_nl_frame.h"
#include "wa_policy.h"

#include <stdio.h>
#include <string.h>

static int failures;
static int checks;

#define CHECK(cond, msg)                                              \
    do {                                                              \
        checks++;                                                     \
        if (!(cond)) {                                                \
            failures++;                                               \
            printf("FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__);  \
        }                                                             \
    } while (0)

static void mk_row(char *buf, size_t len, uint64_t ctid, uint32_t src, uint32_t dst,
                   unsigned proto, int wan, int dir_reply) {
    char s[16], t[16];
    wa_ip_to_str(src, s, sizeof s);
    wa_ip_to_str(dst, t, sizeof t);
    uint32_t mark = wan < 0 ? 0u : (wan == 0 ? WA_WAN0_MARK : WA_WAN1_MARK);
    snprintf(buf, len, "%llx %u %s %s %s mark=0x%08x inbound=0 assured=1\n",
             (unsigned long long)ctid, proto, s, t, dir_reply ? "reply" : "orig", mark);
}

static void mk_dump(char *out, size_t len, const char *rows, int count) {
    snprintf(out, len, WA_DUMP_HEADER "%d\n%s" WA_DUMP_END "\n", count, rows);
}

static uint32_t ip(const char *s) {
    uint32_t v = 0;
    wa_parse_ipv4(s, &v);
    return v;
}

static wa_assoc *find_assoc(wa_engine *e, uint32_t src) {
    for (int i = 0; i < e->n_assoc; i++)
        if (e->assoc[i].src == src) return &e->assoc[i];
    return NULL;
}

static void base_engine(wa_engine *e, wa_mode mode, const char *seed) {
    wa_engine_init(e, mode, 2, 1, 0.0, 15.0, 2.0, seed, 0x1234567ULL);
    wa_domain_set_lan(e, ip("192.168.1.0"), ip("255.255.255.0"));
}

static void set_native(wa_engine *e, int unit, int up) {
    wa_wan_facts f;
    memset(&f, 0, sizeof f);
    f.enable = 1;
    f.state_t = up ? 2 : 3;
    snprintf(f.ip, sizeof f.ip, "%s", unit == 0 ? "203.0.113.2" : "198.51.100.2");
    snprintf(f.gw, sizeof f.gw, "%s", unit == 0 ? "203.0.113.1" : "198.51.100.1");
    snprintf(f.ifname, sizeof f.ifname, "%s", unit == 0 ? "eth0" : "eth2");
    f.route_ready = 1;
    wa_health_set_native(e, unit, &f);
}

static void both_up(wa_engine *e) {
    set_native(e, 0, 1);
    set_native(e, 1, 1);
}

static void test_simultaneous_first_connections(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    int wan = -1;
    for (int i = 0; i < 6; i++) {
        wa_admission r = wa_admission_decision(&e, 100.0, ip("192.168.1.50"), ip("93.184.216.34"),
                                               (i % 2) ? 17 : 6, 4000 + i, 443, 0, 100 + i);
        CHECK(r.action == WA_ACT_MARK, "simultaneous packet marked");
        if (wan < 0) wan = r.wan;
        CHECK(r.wan == wan, "simultaneous packets share one WAN");
    }
    CHECK(e.n_assoc == 1, "one association created");
}

static void test_mark_preserved(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 1.0, ip("192.168.1.60"), ip("93.184.216.34"), 6,
                                           4000, 80, 0x00000005u, 1);
    CHECK(r.action == WA_ACT_MARK, "marked");
    CHECK((r.out_mark & WA_NONWAN_MASK) == 0x00000005u, "unrelated mark bits preserved");
    CHECK(wa_wan_from_mark(r.out_mark) == r.wan, "wan bits set");
}

static void test_exclusions(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_domain_add_pin(&e, ip("192.168.1.70"), 0xffffffffu, 0, 0);
    wa_domain_add_vpn(&e, ip("74.122.141.0"), ip("255.255.255.0"));
    const char *want[] = {"gui-pin", "local-or-lan-dest", "vpn-dest", "non-lan-source",
                          "already-wan-marked", "proto-unsupported"};
    wa_admission r;
    r = wa_admission_decision(&e, 1.0, ip("192.168.1.70"), ip("93.184.216.34"), 6, 1, 80, 0, 1);
    CHECK(strcmp(r.reason, want[0]) == 0, "pinned source excluded");
    r = wa_admission_decision(&e, 1.0, ip("192.168.1.71"), ip("192.168.1.1"), 6, 1, 80, 0, 2);
    CHECK(strcmp(r.reason, want[1]) == 0, "local destination excluded");
    r = wa_admission_decision(&e, 1.0, ip("192.168.1.71"), ip("74.122.141.5"), 6, 1, 80, 0, 3);
    CHECK(strcmp(r.reason, want[2]) == 0, "vpn destination excluded");
    r = wa_admission_decision(&e, 1.0, ip("203.0.113.9"), ip("1.1.1.1"), 6, 1, 80, 0, 4);
    CHECK(strcmp(r.reason, want[3]) == 0, "non-lan source excluded");
    r = wa_admission_decision(&e, 1.0, ip("192.168.1.71"), ip("1.1.1.1"), 6, 1, 80,
                              WA_WAN0_MARK, 5);
    CHECK(strcmp(r.reason, want[4]) == 0, "already-marked excluded");
    r = wa_admission_decision(&e, 1.0, ip("192.168.1.71"), ip("1.1.1.1"), 1, 0, 0, 0, 6);
    CHECK(strcmp(r.reason, want[5]) == 0, "icmp excluded");
    CHECK(e.n_assoc == 0, "no association from excluded traffic");
}

static void test_delayed_unconfirmed_packet_is_retained(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 0.0, ip("192.168.1.55"), ip("93.184.216.34"), 17,
                                           5000, 443, 0, 77);
    CHECK(r.action == WA_ACT_MARK, "admitted");

    char dump[256];
    mk_dump(dump, sizeof dump, "", 0);
    CHECK(wa_reconcile(&e, 3.0, dump, 1) == 0, "empty dump is valid");
    CHECK(e.n_assoc == 1, "association retained while packet unconfirmed");
    CHECK(e.uncertain == 1, "unconfirmed admission reports uncertainty");
    CHECK(wa_tick(&e, 3.0) == 0, "nothing expires");

    char row[256], rows[256];
    mk_row(row, sizeof row, 77, ip("192.168.1.55"), ip("93.184.216.34"), 17, r.wan, 0);
    snprintf(rows, sizeof rows, "%s", row);
    mk_dump(dump, sizeof dump, rows, 1);
    CHECK(wa_reconcile(&e, 3.1, dump, 1) == 0, "confirmed dump parsed");
    CHECK(e.uncertain == 0, "uncertainty cleared after confirmation");
    CHECK(e.n_assoc == 1, "association still present");
}

static void test_quiet_established_retained_and_expiry(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 0.0, ip("192.168.1.56"), ip("93.184.216.34"), 6,
                                           5000, 443, 0, 88);
    char row[256], dump[256];
    mk_row(row, sizeof row, 88, ip("192.168.1.56"), ip("93.184.216.34"), 6, r.wan, 0);
    mk_dump(dump, sizeof dump, row, 1);
    CHECK(wa_reconcile(&e, 1.0, dump, 1) == 0, "state confirmed");
    CHECK(wa_tick(&e, 3600.0) == 0, "quiet established flow not expired"); /* stale -> no expiry */
    CHECK(wa_reconcile(&e, 3600.0, dump, 1) == 0, "fresh reconcile");
    CHECK(wa_tick(&e, 3600.0) == 0, "still retained with live state");
    CHECK(e.n_assoc == 1, "association retained");

    mk_dump(dump, sizeof dump, "", 0);
    CHECK(wa_reconcile(&e, 3601.0, dump, 1) == 0, "empty confirmed");
    CHECK(wa_tick(&e, 3601.0) == 1, "association expires once confirmed empty");
    CHECK(e.n_assoc == 0, "association removed");
}

static void test_invalid_dumps_never_expire(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 0.0, ip("192.168.1.57"), ip("93.184.216.34"), 6,
                                           5000, 443, 0, 99);
    char row[256], dump[256];
    mk_row(row, sizeof row, 99, ip("192.168.1.57"), ip("93.184.216.34"), 6, r.wan, 0);
    mk_dump(dump, sizeof dump, row, 1);
    wa_reconcile(&e, 1.0, dump, 1);

    CHECK(wa_reconcile(&e, 2.0, "", 0) != 0, "read error rejected");
    CHECK(e.n_assoc == 1, "retained after read error");
    CHECK(wa_reconcile(&e, 2.1, WA_DUMP_HEADER "0\n", 1) != 0, "missing sentinel rejected");
    CHECK(e.n_assoc == 1, "retained after truncated dump");
    CHECK(wa_reconcile(&e, 2.2, WA_DUMP_HEADER "5\n" WA_DUMP_END "\n", 1) != 0,
          "count mismatch rejected");
    CHECK(e.n_assoc == 1, "retained after miscounted dump");
    CHECK(wa_tick(&e, 300.0) == 0, "stale reconcile does not expire");
    CHECK(e.n_assoc == 1, "retained");
}

static void test_affinity_returns_to_preference(void) {
    wa_engine e;
    base_engine(&e, WA_AFFINITY, "aa:bb:cc:dd:ee:ff");
    both_up(&e);
    uint32_t src = 0;
    int wan = -1;
    for (int i = 2; i < 250 && wan != 0; i++) {
        char s[16];
        snprintf(s, sizeof s, "192.168.1.%d", i);
        src = ip(s);
        wa_admission r = wa_admission_decision(&e, 1.0, src, ip("93.184.216.34"), 6, 1000 + i, 443,
                                               0, (uint64_t)(1000 + i));
        wan = r.wan;
    }
    CHECK(wan == 0, "found a source preferring WAN0");
    wa_assoc *a = NULL;
    for (int i = 0; i < e.n_assoc; i++)
        if (e.assoc[i].src == src) a = &e.assoc[i];
    CHECK(a && a->preferred == 0, "preference recorded");

    set_native(&e, 0, 0);
    set_native(&e, 1, 1);
    wa_admission r2 = wa_admission_decision(&e, 2.0, src, ip("1.1.1.1"), 6, 2000, 80, 0, 2000);
    CHECK(r2.wan == 1, "temporary failover to WAN1");
    CHECK(a->preferred == 0, "preference held through failure");

    set_native(&e, 0, 1);
    wa_admission r3 = wa_admission_decision(&e, 3.0, src, ip("1.1.1.1"), 6, 3000, 80, 0, 3000);
    CHECK(r3.wan == 0, "returned to persistent preference");
    CHECK(strcmp(a->reason, "affinity-return") == 0, "reason is affinity-return");
}

static void test_sticky_does_not_return(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    uint32_t src = ip("192.168.1.40");
    set_native(&e, 1, 0);
    wa_admission r1 = wa_admission_decision(&e, 1.0, src, ip("93.184.216.34"), 6, 1000, 443, 0, 1);
    CHECK(r1.wan == 0, "placed on WAN0");

    set_native(&e, 0, 0);
    set_native(&e, 1, 1);
    wa_admission r2 = wa_admission_decision(&e, 2.0, src, ip("93.184.216.34"), 6, 1001, 443, 0, 2);
    CHECK(r2.wan == 1, "failed over to WAN1");

    set_native(&e, 0, 1);
    wa_admission r3 = wa_admission_decision(&e, 3.0, src, ip("93.184.216.34"), 6, 1002, 443, 0, 3);
    CHECK(r3.wan == 1, "sticky retains the backup after recovery");
}

static void test_reconstruct_single_and_mixed(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    char rows[512], dump[1024], r1[160], r2[160], r3[160];
    mk_row(r1, sizeof r1, 1, ip("192.168.1.90"), ip("93.184.216.34"), 6, 0, 0);
    mk_row(r2, sizeof r2, 2, ip("192.168.1.90"), ip("1.1.1.1"), 17, 0, 0);
    mk_row(r3, sizeof r3, 3, ip("192.168.1.92"), ip("93.184.216.34"), 6, 0, 0);
    snprintf(rows, sizeof rows, "%s%s%s", r1, r2, r3);
    mk_dump(dump, sizeof dump, rows, 3);
    CHECK(wa_reconcile(&e, 500.0, dump, 1) == 0, "dump parsed");
    wa_reconstruct(&e);
    wa_assoc *a = find_assoc(&e, ip("192.168.1.90"));
    CHECK(a && a->wan == 0 && a->adopted, "single-WAN source reconstructed");

    char m1[160], m2[160], mrows[512], mdump[1024];
    mk_row(m1, sizeof m1, 10, ip("192.168.1.91"), ip("93.184.216.34"), 6, 0, 0);
    mk_row(m2, sizeof m2, 11, ip("192.168.1.91"), ip("1.1.1.1"), 6, 1, 0);
    snprintf(mrows, sizeof mrows, "%s%s", m1, m2);
    mk_dump(mdump, sizeof mdump, mrows, 2);
    wa_engine e2;
    base_engine(&e2, WA_STICKY, NULL);
    both_up(&e2);
    CHECK(wa_reconcile(&e2, 500.0, mdump, 1) == 0, "mixed dump parsed");
    wa_reconstruct(&e2);
    CHECK(find_assoc(&e2, ip("192.168.1.91")) == NULL, "mixed-WAN source not adopted");
    wa_admission r = wa_admission_decision(&e2, 501.0, ip("192.168.1.91"), ip("1.1.1.1"), 6, 9, 80,
                                           0, 900);
    CHECK(strcmp(r.reason, "transitional-adoption") == 0, "mixed-WAN source deferred");
}

static void test_event_lifecycle(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 0.0, ip("192.168.1.65"), ip("93.184.216.34"), 6,
                                           5000, 443, 0, 55);
    char row[256], dump[256];
    mk_row(row, sizeof row, 55, ip("192.168.1.65"), ip("93.184.216.34"), 6, r.wan, 0);
    mk_dump(dump, sizeof dump, row, 1);
    wa_reconcile(&e, 1.0, dump, 1);
    CHECK(e.n_assoc == 1, "association present with live state");

    wa_event_destroy(&e, 1.5, 55); /* duplicate-safe */
    wa_event_destroy(&e, 1.6, 55);
    mk_dump(dump, sizeof dump, "", 0);
    CHECK(wa_reconcile(&e, 2.0, dump, 1) == 0, "empty valid dump");
    CHECK(wa_tick(&e, 2.0) == 1, "expires after last state confirmed gone");
    CHECK(e.n_assoc == 0, "association removed after lifecycle end");
}

static void test_destroy_alone_does_not_expire(void) {
    wa_engine e;
    base_engine(&e, WA_STICKY, NULL);
    both_up(&e);
    wa_admission r = wa_admission_decision(&e, 0.0, ip("192.168.1.67"), ip("93.184.216.34"), 6,
                                           5002, 443, 0, 67);
    char row[256], dump[256];
    mk_row(row, sizeof row, 67, ip("192.168.1.67"), ip("93.184.216.34"), 6, r.wan, 0);
    mk_dump(dump, sizeof dump, row, 1);
    CHECK(wa_reconcile(&e, 1.0, dump, 1) == 0, "state confirmed");
    wa_event_destroy(&e, 2.0, 67);
    CHECK(wa_tick(&e, 2.0) == 0, "a lone DESTROY never expires");
    CHECK(e.n_assoc == 1, "association retained after lone DESTROY");
}

static void test_affinity_golden_vectors(void) {
    /* Must match wan_affinity/selection.py AffinitySelector("seed-x", (2,1));
     * see tests/test_selection.py. One algorithm, one set of vectors. */
    wa_engine e;
    base_engine(&e, WA_AFFINITY, "seed-x");
    both_up(&e);
    wa_admission a = wa_admission_decision(&e, 1.0, ip("192.168.1.10"), ip("93.184.216.34"), 6,
                                           1000, 443, 0, 1);
    CHECK(a.wan == 0, "golden: 192.168.1.10 -> WAN0");
    wa_admission b = wa_admission_decision(&e, 1.0, ip("192.168.1.99"), ip("93.184.216.34"), 6,
                                           1001, 443, 0, 2);
    CHECK(b.wan == 1, "golden: 192.168.1.99 -> WAN1");
}

static void test_snapshot_expiry_and_recovery_admission(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    const char *s="version=1\npublisher=wan-watchdog\npublisher_boot_id=test-boot\nseq=1\npublished_mono=100\nwan0.state=eligible\nwan0.eligible=0\nwan0.recovered=0\nwan1.state=eligible\nwan1.eligible=1\nwan1.recovered=0\n";
    CHECK(wa_health_apply_snapshot(&e,s,100)==1,"complete snapshot accepted");
    CHECK(wa_health_eligibility(&e,0)==WA_UNKNOWN,"acceptable but recovery-held WAN not admitted");
    CHECK(wa_health_eligibility(&e,1)==WA_ELIGIBLE,"other eligible WAN available");
    CHECK(wa_health_apply_snapshot(&e,s,116)==0,"rereading expired snapshot does not renew freshness");
    wa_health_refresh(&e,116);
    CHECK(!e.health.enhanced,"old snapshot expires by publication time");
    CHECK(wa_health_eligibility(&e,0)==WA_UNKNOWN,"stale excluded WAN not readmitted");
    set_native(&e,1,0);
    CHECK(wa_health_eligibility(&e,1)==WA_UNUSABLE,"native disconnection is authoritative");
}

static void test_transitional_source_recovers_after_complete_idle(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    char row[256],dump[512];
    mk_row(row,sizeof row,77,ip("192.168.1.51"),ip("1.1.1.1"),6,-1,0);
    mk_dump(dump,sizeof dump,row,1);wa_reconcile(&e,1,dump,1);wa_reconstruct(&e);
    CHECK(e.n_transitional==1,"unattributed old source deferred");
    mk_dump(dump,sizeof dump,"",0);wa_reconcile(&e,2,dump,1);
    CHECK(e.n_transitional==0,"verified full idle clears transitional adoption");
    wa_admission a=wa_admission_decision(&e,3,ip("192.168.1.51"),ip("1.1.1.1"),6,1234,443,0,78);
    CHECK(a.action==WA_ACT_MARK,"source can be admitted after idle");
}

static void test_invalid_dump_never_uses_old_empty_confirmation(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);e.idle_seconds=10;
    wa_admission a=wa_admission_decision(&e,1,ip("192.168.1.51"),ip("1.1.1.1"),6,1234,443,0,1);
    char row[256],dump[512];
    mk_row(row,sizeof row,1,ip("192.168.1.51"),ip("1.1.1.1"),6,a.wan,0);
    mk_dump(dump,sizeof dump,row,1);wa_reconcile(&e,2,dump,1);
    mk_dump(dump,sizeof dump,"",0);wa_reconcile(&e,3,dump,1);
    wa_reconcile(&e,13,NULL,0);
    CHECK(wa_tick(&e,13)==0,"failed current read prevents expiry from an older empty read");
    CHECK(wa_reconcile(&e,14,WA_DUMP_HEADER "0garbage\n" WA_DUMP_END "\n",1)!=0,"non-numeric count rejected");
}

static void test_full_state_table_retains_pending(void) {
    static wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    for(int i=0;i<WA_MAX_STATES;i++) {
        e.state[i].ctid=(uint64_t)i+1;e.state[i].src=ip("192.168.1.51");
        e.state[i].dst=ip("93.184.216.34");e.state[i].proto=6;
        e.state[i].wan=0;e.state[i].provisional=1;
    }
    e.n_state=WA_MAX_STATES;
    wa_admission a=wa_admission_decision(&e,1,ip("192.168.1.51"),ip("1.1.1.1"),6,1234,443,0,10000);
    CHECK(a.action==WA_ACT_PASS,"untracked admission refused at capacity");
    CHECK(e.n_state==WA_MAX_STATES && e.uncertain,"capacity visible, no pending state lost");
    static char rows[700000],dump[700100];size_t used=0;
    for(int i=0;i<WA_MAX_STATES;i++) {
        char row[256];mk_row(row,sizeof row,10000+i,ip("192.168.1.51"),ip("1.1.1.1"),6,0,0);
        size_t n=strlen(row);memcpy(rows+used,row,n);used+=n;
    }
    rows[used]=0;mk_dump(dump,sizeof dump,rows,WA_MAX_STATES);
    CHECK(wa_reconcile(&e,2,dump,1)!=0,"dump plus pending overflow rejected");
    CHECK(e.n_state==WA_MAX_STATES && e.state[0].ctid==1,"overflow keeps previous pending table intact");
    uint32_t parsed;
    CHECK(wa_parse_ipv4("192.168.1.51;id",&parsed)!=0,"IPv4 parser rejects command suffix");
}

static void test_exact_tuple_snapshot_confirmation(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    uint32_t src=ip("192.168.1.51"),dst=ip("93.184.216.34");
    uint64_t token=wa_flow_token(&e,6,src,dst,50000,443);
    wa_admission a=wa_admission_decision(&e,1,src,dst,6,50000,443,0,token);
    CHECK(wa_flow_token(&e,6,src,dst,50000,443)==token,"retransmission keeps exact tuple identity");
    CHECK(wa_flow_token(&e,6,src,dst,50001,443)!=token,"different source ports never alias");
    CHECK(wa_flow_token(&e,17,src,dst,50000,443)!=token,"TCP and QUIC tuples never alias");
    char row[256],dump[512],base[200];mk_row(base,sizeof base,token,src,dst,6,a.wan,0);
    base[strlen(base)-1]=0;snprintf(row,sizeof row,"%s sport=50000 dport=443\n",base);
    mk_dump(dump,sizeof dump,row,1);
    CHECK(!wa_reconcile(&e,2,dump,1),"full tuple dump confirms pending admission");
    CHECK(!e.state[0].provisional && e.state[0].sport==50000,"ports survive reconciliation");
    CHECK(wa_flow_token(&e,6,src,dst,50000,443)==token,"confirmed tuple keeps identity");
    mk_dump(dump,sizeof dump,"",0);wa_reconcile(&e,3,dump,1);wa_tick(&e,3);
    CHECK(e.n_assoc==0,"validated empty tuple snapshot expires association");
    CHECK(wa_flow_token(&e,6,src,dst,50000,443)!=token,"reused tuple gets a fresh token after retirement");
}

static void test_native_conntrack_rows_and_actual_egress(void) {
    const char *line="ipv4 2 tcp 6 1471 ESTABLISHED src=192.168.1.51 dst=52.110.2.81 sport=46752 dport=443 src=52.110.2.81 dst=198.51.100.2 sport=443 dport=46752 [ASSURED] mark=2147483648 use=1 id=780278320\n";
    wa_ct_record r;wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    CHECK(wa_parse_native_conntrack(line,&r)==1,"native extended,id row parsed");
    CHECK(r.src==ip("192.168.1.51") && r.dst==ip("52.110.2.81") && r.sport==46752 && r.dport==443,"original tuple independent of reply tuple");
    CHECK(r.kernel_id==780278320 && r.mark==WA_WAN0_MARK,"full native ID and unsigned WAN mark preserved");
    CHECK(wa_ct_egress(&r,&e.health)==1,"confirmed SNAT egress overrides misleading GUI-pinned connmark");
    CHECK(wa_parse_native_conntrack("ipv4 2 tcp 6 src=192.168.1.51 dst=1.1.1.1 sport=1234 dport=443 id=2 zone=5",&r)<0,"non-default conntrack zones refused");
    CHECK(wa_parse_native_conntrack("ipv4 2 tcp 6 src=192.168.1.51 dst=1.1.1.1 sport=99999 dport=443 id=2",&r)<0,"out-of-range tuple ports refused");
    CHECK(wa_parse_native_conntrack("ipv4 2 udp 17 src=192.168.1.51 dst=1.1.1.1 sport=1234 dport=443",&r)<0,"incomplete native row refused");
    CHECK(wa_parse_native_conntrack("ipv4 2 icmp 1 src=192.168.1.51 dst=1.1.1.1 id=2",&r)==0,"unmanaged protocol ignored");
}

static void test_netlink_unpadded_final_packet(void) {
    unsigned char buffer[400]={0};uint32_t length=159;memcpy(buffer,&length,4);
    size_t offset=0;
    CHECK(wa_nl_frame(buffer,159,&offset)==buffer,"unpadded 159-byte kernel frame accepted once");
    CHECK(offset==159 && !wa_nl_frame(buffer,159,&offset),"final padding cannot underflow remaining length");
    length=28;memcpy(buffer+160,&length,4);offset=0;
    CHECK(wa_nl_frame(buffer,188,&offset)==buffer,"first padded frame accepted");
    CHECK(wa_nl_frame(buffer,188,&offset)==buffer+160,"second coalesced frame accepted");
    CHECK(!wa_nl_frame(buffer,188,&offset),"coalesced datagram stops at exact boundary");
    length=UINT32_MAX;memcpy(buffer,&length,4);offset=0;
    CHECK(!wa_nl_frame(buffer,400,&offset),"oversized message length rejected");
    offset=0;CHECK(!wa_nl_frame(buffer,12,&offset),"truncated netlink header rejected");
}

static void test_complete_lifecycle_view_protects_held_and_cleans_terminal(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    wa_admission a=wa_admission_decision(&e,0,ip("192.168.1.51"),ip("1.1.1.1"),17,63000,443,0,1);
    CHECK(a.action==WA_ACT_MARK,"held packet admitted");
    wa_state held=e.state[0];held.provisional=0; /* observed in the kernel's U table */
    CHECK(!wa_reconcile_view(&e,1,5,&held,1,1),"U-then-C view sees a packet held beyond warning threshold");
    CHECK(e.n_state==1 && e.n_assoc==1 && !e.uncertain,"observed unconfirmed kernel entry retains its association");
    CHECK(!wa_tick(&e,5),"a real held packet never expires by time alone");
    CHECK(!wa_reconcile_view(&e,5.1,6,NULL,0,1),"absence from both tables accepted after drop");
    CHECK(wa_tick(&e,6)==1 && !e.n_state && !e.n_assoc,"terminally absent held packet releases idle source");
    wa_admission b=wa_admission_decision(&e,7,ip("192.168.1.51"),ip("1.1.1.1"),6,63001,443,0,2);
    CHECK(b.action==WA_ACT_MARK,"fresh short-lived connection admitted");
    CHECK(!wa_reconcile_view(&e,7.1,7.2,NULL,0,1),"finished-before-first-dump admission absent from U and C");
    CHECK(wa_tick(&e,7.2)==1 && !e.n_assoc,"never-confirmed completed admission cleans without a TTL");
}

static void test_async_snapshot_cutoff_and_failed_view(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    wa_admission_decision(&e,2,ip("192.168.1.51"),ip("1.1.1.1"),6,63000,443,0,1);
    CHECK(!wa_reconcile_view(&e,1,3,NULL,0,1),"asynchronous older empty snapshot accepted");
    CHECK(e.n_state==1 && e.state[0].provisional && !wa_tick(&e,3),"admission after collection began survives an empty older view");
    wa_state old=e.state[0];old.provisional=0;old.wan=(int8_t)(1-old.wan);
    CHECK(!wa_reconcile_view(&e,1,3.1,&old,1,1),"older same-tuple snapshot merged");
    CHECK(e.state[0].provisional && e.state[0].wan!=old.wan,"old snapshot cannot overwrite a newer admission");
    CHECK(wa_reconcile_view(&e,3.2,3.3,NULL,0,0)!=0 && e.n_state==1,"partial U or C read cannot retire pending state");
    CHECK(!wa_tick(&e,3.3),"invalid full view inhibits expiry");
    CHECK(wa_reconcile_view(&e,0,30,NULL,0,1)!=0 && e.n_state==1,"late worker result rejected without expiry");
    CHECK(!wa_reconcile_view(&e,4,4.1,NULL,0,1) && wa_tick(&e,4.1)==1,"fresh complete view resumes terminal cleanup");
}

static void test_unconfirmed_rows_and_migration_union(void) {
    wa_ct_record u,c;static wa_ct_view view;memset(&view,0,sizeof view);
    const char *unconfirmed="udp 17 0 src=192.168.1.51 dst=1.1.1.1 sport=63000 dport=443 src=1.1.1.1 dst=192.168.1.51 sport=443 dport=63000 mark=0 use=1\n";
    const char *confirmed="ipv4 2 udp 17 30 src=192.168.1.51 dst=1.1.1.1 sport=63000 dport=443 src=1.1.1.1 dst=203.0.113.2 sport=443 dport=63000 mark=2147483648 use=1 id=77\n";
    CHECK(wa_parse_unconfirmed_conntrack(unconfirmed,&u)==1 && u.unconfirmed && !u.kernel_id,"firmware U dump without extended ID supported explicitly");
    CHECK(wa_parse_native_conntrack(unconfirmed,&c)<0,"confirmed dump still requires complete ID metadata");
    CHECK(!wa_ct_view_add(&view,&u) && view.count==1,"unconfirmed tuple included in lifecycle view");
    CHECK(wa_parse_native_conntrack(confirmed,&c)==1 && !wa_ct_view_add(&view,&c),"U-to-C migration merged");
    CHECK(view.count==1 && !view.rows[0].unconfirmed && view.rows[0].reply_dst==ip("203.0.113.2"),"migration keeps one tuple with confirmed NAT evidence");
    CHECK(!wa_ct_view_add(&view,&u) && !view.rows[0].unconfirmed,"duplicate U record cannot erase confirmed attribution");
    CHECK(wa_parse_unconfirmed_conntrack("udp 17 0 src=192.168.1.51 dst=1.1.1.1",&u)<0,"partial U tuple refused");
}

static void test_policy_exclusions(void) {
    uint32_t s,sm,d,dm;
    CHECK(!wa_policy_exclusion("150: from all fwmark 0x80000000/0xf0000000 lookup wan0\n",&s,&sm,&d,&dm),"native WAN marks retain affinity coverage");
    CHECK(!wa_policy_exclusion("90: from all to 10.6.0.2 lookup main\n",&s,&sm,&d,&dm),"main routing remains native");
    CHECK(wa_policy_exclusion("1000: from 192.168.1.50 to 203.0.113.0/24 lookup ovpnc1\n",&s,&sm,&d,&dm)==1 && s==ip("192.168.1.50") && sm==0xffffffffu && d==ip("203.0.113.0") && dm==0xffffff00u,"VPN selectors projected conservatively");
    CHECK(wa_policy_exclusion("1000: from all fwmark 0x10000/0xff0000 lookup wgc1\n",&s,&sm,&d,&dm)==1 && !sm && !dm,"unknown full-tunnel mark policy bypasses all sources");
    CHECK(wa_policy_exclusion("86: from 203.0.113.1 to 8.8.8.8 lookup 20186\n",&s,&sm,&d,&dm)==1 && s==ip("203.0.113.1") && d==ip("8.8.8.8"),"watchdog probes exclude only their own selectors");
    CHECK(wa_policy_exclusion("1000: not from 192.168.1.0/24 lookup ovpnc1\n",&s,&sm,&d,&dm)==1 && !sm && !dm,"inverted policy cannot accidentally admit VPN sources");
    CHECK(wa_policy_exclusion("1000: from all blackhole\n",&s,&sm,&d,&dm)==1 && !sm && !dm,"unknown routing actions preserve policy");
    CHECK(wa_policy_exclusion("1000: from all lookup wgc1",&s,&sm,&d,&dm)<0,"truncated policy rejected");
}

static void test_live_unpin_adoption_retains_failover_decision(void) {
    wa_engine e;base_engine(&e,WA_STICKY,NULL);both_up(&e);
    uint32_t src=ip("192.168.1.51");
    wa_domain_add_pin(&e,src,0xffffffffu,0,0);
    wa_state old={.ctid=1,.src=src,.dst=0x01010101u,.sport=61000,.dport=443,.proto=6,.wan=1};
    CHECK(!wa_reconcile_view(&e,1,2,&old,1,1),"pinned connection collected without assignment");
    wa_reconstruct(&e);
    CHECK(!find_assoc(&e,src),"GUI pin remains excluded from sticky policy");
    e.domain.n_pins=0;
    wa_reconstruct(&e);
    CHECK(find_assoc(&e,src) && find_assoc(&e,src)->wan==1,"live unpin adopts surviving WAN instead of randomizing");
    set_native(&e,1,0);
    wa_admission r=wa_admission_decision(&e,3,src,0x08080808u,6,61001,443,0,2);
    CHECK(r.action==WA_ACT_MARK && r.wan==0,"new flow fails over while old flow remains");
    wa_reconstruct(&e);
    CHECK(find_assoc(&e,src) && find_assoc(&e,src)->wan==0 && !e.n_transitional,"reconciliation preserves intentional mixed-WAN failover");
}

int main(void) {
    test_policy_exclusions();
    test_live_unpin_adoption_retains_failover_decision();
    test_simultaneous_first_connections();
    test_mark_preserved();
    test_exclusions();
    test_delayed_unconfirmed_packet_is_retained();
    test_quiet_established_retained_and_expiry();
    test_invalid_dumps_never_expire();
    test_affinity_returns_to_preference();
    test_sticky_does_not_return();
    test_reconstruct_single_and_mixed();
    test_event_lifecycle();
    test_destroy_alone_does_not_expire();
    test_affinity_golden_vectors();
    test_snapshot_expiry_and_recovery_admission();
    test_transitional_source_recovers_after_complete_idle();
    test_invalid_dump_never_uses_old_empty_confirmation();
    test_full_state_table_retains_pending();
    test_exact_tuple_snapshot_confirmation();
    test_native_conntrack_rows_and_actual_egress();
    test_netlink_unpadded_final_packet();
    test_complete_lifecycle_view_protects_held_and_cleans_terminal();
    test_async_snapshot_cutoff_and_failed_view();
    test_unconfirmed_rows_and_migration_union();

    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
