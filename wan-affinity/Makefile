CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror
BUILD := build
HEALTH = src/wa_health_client.c ../common/wan-health/wh_protocol.c ../common/wan-health/wh_socket.c
HEALTH_INC = -I../common/wan-health

# Cross toolchain for the router (aarch64, musl static). Override if you have
# another one; see BUILDING.md.
ROUTER_CC ?= zig cc -target aarch64-linux-musl
ROUTER_LDFLAGS ?= -static

.PHONY: all host-tests router router-test clean
all: host-tests

host-tests: $(BUILD)/test_engine $(BUILD)/test_worker
	$(BUILD)/test_engine
	$(BUILD)/test_worker

$(BUILD)/test_worker: src/wa_worker.c src/wa_worker.h tests/c/test_worker.c
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Isrc tests/c/test_worker.c src/wa_worker.c -o $@

$(BUILD)/test_engine: src/wa_engine.c src/wa_engine.h src/wa_conntrack.c src/wa_conntrack.h src/wa_nl_frame.h src/wa_policy.h tests/c/test_engine.c
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Isrc tests/c/test_engine.c src/wa_engine.c src/wa_conntrack.c -o $@

router: $(BUILD)/wan-affinity

$(BUILD)/wan-affinity: src/wan_affinity.c src/wa_engine.c src/wa_engine.h src/wa_conntrack.c src/wa_conntrack.h src/wa_nl_frame.h src/wa_policy.h src/wa_worker.c src/wa_worker.h $(HEALTH) src/wa_health_client.h ../common/wan-health/wh_protocol.h ../common/wan-health/wh_socket.h
	@mkdir -p $(BUILD)
	$(ROUTER_CC) $(CFLAGS) $(ROUTER_LDFLAGS) -Isrc $(HEALTH_INC) src/wan_affinity.c src/wa_engine.c src/wa_conntrack.c src/wa_worker.c $(HEALTH) -o $@
	@echo "built $@ (aarch64)"

router-test: $(BUILD)/wan-affinity-test
$(BUILD)/wan-affinity-test: $(BUILD)/wan-affinity
	$(ROUTER_CC) $(CFLAGS) $(ROUTER_LDFLAGS) -DWA_TESTING -Isrc $(HEALTH_INC) src/wan_affinity.c src/wa_engine.c src/wa_conntrack.c src/wa_worker.c $(HEALTH) -o $@

clean:
	rm -rf $(BUILD)

.PHONY: bundle
bundle: router
	rm -rf $(BUILD)/bundle
	mkdir -p $(BUILD)/bundle/docs $(BUILD)/bundle/LICENSES
	cp $(BUILD)/wan-affinity wan-affinity.sh wan-affinity.conf.example hooks.sh install.sh rollback.sh README.md ../LICENSE $(BUILD)/bundle/
	cp BUILDING.md $(BUILD)/bundle/
	cp docs/QUALIFICATION-2026-10-15.md docs/HEALTH-SOCKET.md docs/THROUGHPUT-2026-10-15.md $(BUILD)/bundle/docs/
	cp LICENSES/musl-COPYRIGHT.txt $(BUILD)/bundle/LICENSES/
	chmod 700 $(BUILD)/bundle/wan-affinity $(BUILD)/bundle/*.sh
	tar -czf $(BUILD)/wan-affinity-aarch64.tar.gz -C $(BUILD)/bundle .

# Consumer-side shared-health qualification; intentionally outside watchdog.
.PHONY: router-health-tests
router-health-tests: $(BUILD)/test_health_socket $(BUILD)/health-fixture
$(BUILD)/test_health_socket: tests/c/test_health_socket.c src/wa_engine.c src/wa_health_client.h $(HEALTH)
	@mkdir -p $(BUILD)
	$(ROUTER_CC) $(CFLAGS) $(ROUTER_LDFLAGS) -Isrc $(HEALTH_INC) $(filter %.c,$^) -o $@
$(BUILD)/health-fixture: tests/c/health_fixture.c ../common/wan-health/wh_protocol.c ../common/wan-health/wh_socket.c ../common/wan-health/wh_protocol.h ../common/wan-health/wh_socket.h
	@mkdir -p $(BUILD)
	$(ROUTER_CC) $(CFLAGS) $(ROUTER_LDFLAGS) -Isrc $(HEALTH_INC) $(filter %.c,$^) -o $@
