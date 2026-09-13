// SPDX-License-Identifier: Apache-2.0
// Host-only, deterministic fixtures. No board, serial, Wi-Fi, or real CSI.
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <initializer_list>
#include "../cascadia_csi/CsiPacket.h"
int main() {
  uint8_t action[23] = {127,0x18,0xfe,0x34,1,2,3,4,221,9,0x18,0xfe,0x34,4,2,0x78,0x56,0x34,0x12};
  uint32_t sequence = 99;
  unsigned checks = 0;
  assert(!cascadia::decodeSequence(nullptr,19,sequence)); ++checks;
  for (size_t size=0; size<19; ++size) {
    assert(!cascadia::decodeSequence(action,size,sequence)); ++checks;
    assert(sequence == 99); ++checks;
  }
  assert(cascadia::decodeSequence(action,19,sequence)); ++checks;
  assert(sequence == 0x12345678); ++checks;
  // A callback may include a trailing FCS; the element length still fixes the 4-byte body.
  assert(cascadia::decodeSequence(action,sizeof(action),sequence)); ++checks;
  action[14]=1; assert(cascadia::decodeSequence(action,19,sequence)); ++checks; action[14]=2;
  const size_t guarded[] = {0,1,2,3,8,9,10,11,12,13,14};
  for (size_t i : guarded) {
    const uint8_t saved=action[i]; action[i]=0;
    assert(!cascadia::decodeSequence(action,19,sequence)); ++checks;
    action[i]=saved;
  }
  for (unsigned version : {0u,3u,0x12u,0x82u}) {
    action[14]=uint8_t(version); assert(!cascadia::decodeSequence(action,19,sequence)); ++checks;
  }
  action[14]=2;
  for (uint32_t value : {0u,1u,0x7fffffffu,0x80000000u,0xffffffffu}) {
    uint8_t encoded[4]; cascadia::encodeSequence(value,encoded);
    memcpy(action+15,encoded,4);
    assert(cascadia::decodeSequence(action,19,sequence)); ++checks;
    assert(sequence == value); ++checks;
  }
  printf("PASS %u packet framing checks; synthetic host-only fixtures, no CSI claimed\n",checks);
}
