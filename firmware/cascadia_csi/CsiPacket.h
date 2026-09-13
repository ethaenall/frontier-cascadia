// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stddef.h>
#include <stdint.h>
namespace cascadia {
// Official ESP-NOW action-body layout: category/OUI/random = 8 bytes;
// vendor element ID/length/OUI/type/version = 7 bytes, then application body.
// Unlike upstream's unchecked payload+15 cast, reject every invalid boundary.
inline bool decodeSequence(const uint8_t *p, size_t n, uint32_t &sequence) {
  if (!p || n < 19) return false;
  if (p[0] != 127 || p[1] != 0x18 || p[2] != 0xfe || p[3] != 0x34) return false;
  if (p[8] != 221 || p[9] != 9 || size_t(p[9]) + 10 > n) return false;
  if (p[10] != 0x18 || p[11] != 0xfe || p[12] != 0x34 || p[13] != 4) return false;
  const uint8_t version = p[14] & 0x0f;
  if ((version != 1 && version != 2) || (p[14] & 0xf0)) return false;
  sequence = uint32_t(p[15]) | (uint32_t(p[16]) << 8) |
             (uint32_t(p[17]) << 16) | (uint32_t(p[18]) << 24);
  return true;
}
inline void encodeSequence(uint32_t sequence, uint8_t (&out)[4]) {
  for (unsigned i = 0; i < 4; ++i) out[i] = uint8_t(sequence >> (8 * i));
}
}
