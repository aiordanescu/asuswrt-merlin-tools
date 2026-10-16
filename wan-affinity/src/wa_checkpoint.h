#ifndef WA_CHECKPOINT_H
#define WA_CHECKPOINT_H
#include "wa_engine.h"
/* Private, versioned same-boot RAM checkpoint, never a public wire format.
 * Restore only after a fresh complete U-then-C observation. Misses are safe. */
int wa_checkpoint_save(const char *path, const wa_engine *e, const uint8_t boot[16],
                       uint64_t profile, uint64_t now_ns);
int wa_checkpoint_load(const char *path, wa_engine *e, const uint8_t boot[16],
                       uint64_t profile, uint64_t now_ns);
#endif
