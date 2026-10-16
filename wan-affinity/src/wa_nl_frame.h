#ifndef WA_NL_FRAME_H
#define WA_NL_FRAME_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Netlink's final message may end before its alignment padding. In particular,
 * NLMSG_NEXT with an unsigned remaining length underflows on a 159-byte frame.
 * Return that last frame once, then stop without touching bytes past recv(). */
static inline const void *wa_nl_frame(const void *buffer,size_t length,size_t *offset) {
    if(*offset>length || length-*offset<16)return NULL;
    const unsigned char *at=(const unsigned char*)buffer+*offset;
    uint32_t message_length;memcpy(&message_length,at,sizeof message_length);
    size_t remaining=length-*offset;
    if(message_length<16 || message_length>remaining){*offset=length;return NULL;}
    size_t step=((size_t)message_length+3u)&~(size_t)3u;
    *offset+=step>remaining?remaining:step;
    return at;
}
#endif
