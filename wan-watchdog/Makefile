BUNDLE_REF ?= main
CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror
ROUTER_CC ?= zig cc -target aarch64-linux-musl
INCLUDES = -Isrc -I../common/wan-health
COMMON = ../common/wan-health/wh_protocol.c
SOCKET = ../common/wan-health/wh_socket.c
.PHONY: host-tests router bundle clean
HEADERS = $(wildcard src/*.h ../common/wan-health/*.h)
MONITOR = src/wan_watchdog.c src/ww_core.c src/ww_probe.c src/ww_worker.c $(COMMON) $(SOCKET)
router: build/wan-watchdog build/wan-healthctl build/test_socket build/test_probe
bundle: build/wan-watchdog build/wan-healthctl
	rm -rf build/bundle
	mkdir -p build/bundle/lib build/bundle/LICENSES
	cp build/wan-watchdog build/wan-healthctl wan-watchdog.sh wan-watchdog-ddns.sh install.sh build/bundle/
	cp wan-watchdog-legacy.sh build/bundle/wan-watchdog-legacy.sh
	cp rollback.sh build/bundle/rollback.sh
	cp lib/ww-adapter.sh build/bundle/lib/
	cp lib/ww-service.sh build/bundle/lib/
	cp ../LICENSE build/bundle/LICENSE
	cp LICENSES/*.txt build/bundle/LICENSES/
	cp BUNDLE-README.md build/bundle/README.md
	cp ../MAINTENANCE-2026-10-15.md build/bundle/MAINTENANCE-2026-10-15.md
	chmod 700 build/bundle build/bundle/wan-watchdog build/bundle/wan-healthctl build/bundle/*.sh build/bundle/lib/*.sh
	cp ../REVIEW-LOW-2026-10-16.md build/bundle/
	python3 ../tools/bundle_links.py wan-watchdog build/bundle --ref "$(BUNDLE_REF)"
	COPYFILE_DISABLE=1 tar --format=ustar -czf build/wan-watchdog-aarch64.tar.gz -C build/bundle .
build/wan-watchdog: $(MONITOR) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/wan-healthctl: src/wan_healthctl.c $(COMMON) $(SOCKET) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/test_socket: tests/monitor/test_socket.c $(COMMON) $(SOCKET) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/test_probe: tests/monitor/test_probe.c src/ww_probe.c $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
host-tests: build/test_core build/replay build/test_worker build/test_runtime
	build/test_core
	build/test_worker
	build/test_runtime
	python3 -m unittest discover -s tests/monitor -p 'test_*.py'
build:
	mkdir -p build
build/test_core: src/ww_core.c tests/monitor/test_core.c $(COMMON) $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
build/replay: src/ww_core.c tests/monitor/replay.c $(COMMON) $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
clean:
	rm -rf build

build/test_worker: tests/monitor/test_worker.c src/ww_worker.c $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
build/test_runtime: tests/monitor/test_runtime.c $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
