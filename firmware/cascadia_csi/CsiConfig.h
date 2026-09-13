// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stdint.h>
// Compile each role explicitly. There is no accidental default transmitter.
#ifndef CASCADIA_ROLE
#error "Build with -DCASCADIA_ROLE=1 (sender) or =2 (receiver)"
#endif
#if CASCADIA_ROLE != 1 && CASCADIA_ROLE != 2
#error "Invalid CASCADIA_ROLE"
#endif
#ifndef CASCADIA_CHANNEL
#define CASCADIA_CHANNEL 6
#endif
#ifndef CASCADIA_SEND_HZ
#define CASCADIA_SEND_HZ 20
#endif
#if CASCADIA_CHANNEL < 1 || CASCADIA_CHANNEL > 11
#error "Select one fixed legal local 2.4 GHz channel from 1..11; never scan"
#endif
#if CASCADIA_SEND_HZ < 1 || CASCADIA_SEND_HZ > 50
#error "Conservative rate bound is 1..50 Hz"
#endif
namespace cascadia {
constexpr uint8_t kSenderMac[6] = {0x02,0xca,0x5c,0xad,0x1a,0x01};
constexpr uint8_t kReceiverMac[6] = {0x02,0xca,0x5c,0xad,0x1a,0x02};
constexpr unsigned kMaxCsiBytes = 640;
constexpr unsigned kQueueDepth = 8;
constexpr char kProfile[] = "esp32-s3-raw-int8-v1";
constexpr char kUpstreamRevision[] = "8633d67152db2808f141cc1595970aa9cf406045";
constexpr char kCsvHeader[] = "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data";
}
