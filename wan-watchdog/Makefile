CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror
ROUTER_CC ?= zig cc -target aarch64-linux-musl
INCLUDES = -Isrc -I../common/wan-health
COMMON = ../common/wan-health/wh_protocol.c
SOCKET = ../common/wan-health/wh_socket.c
.PHONY: host-tests router bundle clean
HEADERS = $(wildcard src/*.h ../common/wan-health/*.h)
NATIVE = src/wan_watchdog.c src/ww_core.c src/ww_probe.c src/ww_worker.c $(COMMON) $(SOCKET)
router: build/wan-watchdog-native build/wan-healthctl build/test_socket build/test_probe
bundle: build/wan-watchdog-native build/wan-healthctl
	rm -rf build/bundle
	mkdir -p build/bundle/lib build/bundle/LICENSES
	cp build/wan-watchdog-native build/wan-healthctl wan-watchdog-native.sh install-native.sh build/bundle/
	cp wan-watchdog.sh build/bundle/wan-watchdog-legacy.sh
	cp rollback-native.sh build/bundle/rollback.sh
	cp lib/ww-adapter.sh build/bundle/lib/
	cp ../LICENSE build/bundle/LICENSE
	cp LICENSES/*.txt build/bundle/LICENSES/
	cp BUNDLE-README.md build/bundle/README.md
	chmod 700 build/bundle build/bundle/wan-watchdog-native build/bundle/wan-healthctl build/bundle/*.sh build/bundle/lib/*.sh
	COPYFILE_DISABLE=1 tar --format=ustar -czf build/wan-watchdog-native-aarch64.tar.gz -C build/bundle .
build/wan-watchdog-native: $(NATIVE) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/wan-healthctl: src/wan_healthctl.c $(COMMON) $(SOCKET) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/test_socket: tests/native/test_socket.c $(COMMON) $(SOCKET) $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
build/test_probe: tests/native/test_probe.c src/ww_probe.c $(HEADERS) | build
	$(ROUTER_CC) $(CFLAGS) -static $(INCLUDES) $(filter %.c,$^) -o $@
host-tests: build/test_core build/replay
	build/test_core
	python3 -m unittest discover -s tests/native -p 'test_*.py'
build:
	mkdir -p build
build/test_core: src/ww_core.c tests/native/test_core.c $(COMMON) $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
build/replay: src/ww_core.c tests/native/replay.c $(COMMON) $(HEADERS) | build
	$(CC) $(CFLAGS) $(INCLUDES) $(filter %.c,$^) -o $@
clean:
	rm -rf build
