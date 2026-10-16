#ifndef WA_HEALTH_CLIENT_H
#define WA_HEALTH_CLIENT_H
#include "wa_engine.h"
#include "wh_socket.h"
enum wa_health_source { WA_HEALTH_AUTO, WA_HEALTH_NATIVE, WA_HEALTH_SOCKET, WA_HEALTH_LEGACY };
typedef struct {
    wh_client client;
    enum wa_health_source source;
    char checkpoint[200];
    int checkpoint_mask;
} wa_health_feed;
void wa_health_feed_init(wa_health_feed *feed, wa_engine *engine, enum wa_health_source source,
                         const char *socket_path, const char *runtime);
int wa_health_feed_step(wa_health_feed *feed, wa_engine *engine, uint64_t now);
/* A collector may update native facts only; enhanced state belongs to parent. */
void wa_health_merge_native(wa_engine *engine, const wa_health *collected);
#endif
