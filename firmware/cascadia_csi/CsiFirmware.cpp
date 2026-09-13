/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 * SPDX-License-Identifier: Apache-2.0
 *
 * Modified for Frontier Cascadia, 2026: Arduino Nano ESP32 ABX00083 port of
 * espressif/esp-csi get-started sender/receiver at revision
 * 8633d67152db2808f141cc1595970aa9cf406045.
 * Changes: board-specific USB CDC, distinct locally administered MACs,
 * unicast/no promiscuous capture, HT20/20Hz, safe sequence parsing,
 * RAW int8 CSI (no gain compensation), bounded queue, non-WiFi-task output,
 * no AP, scanning, credentials, WiFi NVS writes, or automatic firmware resets.
 * This is a CSI acquisition build, not evidence of physical sensing accuracy.
 */
#include <Arduino.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "esp_arduino_version.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "CsiConfig.h"
#include "CsiFirmware.h"
#include "CsiPacket.h"

#if !defined(ARDUINO_NANO_ESP32) || !CONFIG_IDF_TARGET_ESP32S3
#error "This firmware only supports the exact Arduino Nano ESP32 ESP32-S3 target"
#endif
#if !ARDUINO_USB_CDC_ON_BOOT || !CONFIG_ESP_WIFI_CSI_ENABLED
#error "USB CDC and the bundled Wi-Fi CSI support must be enabled"
#endif

namespace {
// This Xtensa toolchain does not declare SharedValue<uint32_t> lock-free.
// Explicit, tiny IDF critical sections give counters defined cross-core access.
template <typename T> class SharedValue {
  portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
  T value;
public:
  explicit SharedValue(T initial) : value(initial) {}
  T load() { portENTER_CRITICAL(&mux); const T copy = value; portEXIT_CRITICAL(&mux); return copy; }
  void store(T next) { portENTER_CRITICAL(&mux); value = next; portEXIT_CRITICAL(&mux); }
  void operator++() { portENTER_CRITICAL(&mux); ++value; portEXIT_CRITICAL(&mux); }
};
using namespace cascadia;
constexpr bool kSender = CASCADIA_ROLE == 1;
constexpr const uint8_t *kLocalMac = kSender ? kSenderMac : kReceiverMac;
constexpr const uint8_t *kPeerMac = kSender ? kReceiverMac : kSenderMac;
const char *failure = nullptr;
esp_err_t failureCode = ESP_OK;
bool ready = false;
bool consoleWasOpen = false;
bool wifiStarted = false;
uint8_t actualMac[6] = {};
uint32_t lastStatsMs = 0;
uint32_t lastSendMs = 0;
uint32_t nextSequence = 0;
SharedValue<uint32_t> csiAccepted{0}, malformedOwn{0}, queueDropped{0};
SharedValue<uint32_t> ownEspNowPackets{0}, txCalls{0}, txErrors{0}, txOk{0}, txFailed{0};
SharedValue<bool> txBusy{false};
uint32_t serialDropped = 0;
uint32_t rowsWritten = 0;

struct CsiFrame {
  wifi_pkt_rx_ctrl_t rx;
  uint32_t sequence;
  uint16_t length;
  bool firstInvalid;
  uint8_t mac[6];
  int8_t samples[kMaxCsiBytes];
};
QueueHandle_t csiQueue = nullptr;
// Static buffers do not use the limited loop-task stack. One loop-task writer.
CsiFrame pendingFrame{};
char csvLine[4096];

bool requireOk(esp_err_t error, const char *operation) {
  if (error == ESP_OK) return true;
  failure = operation;
  failureCode = error;
  ready = false;
  if (wifiStarted) {
    const esp_err_t stopped = esp_wifi_stop();
    wifiStarted = stopped != ESP_OK;
  }
  return false;
}
#define REQUIRE_OK(expression) do { if (!requireOk((expression), #expression)) return false; } while (0)

void onSend(const esp_now_send_info_t *, esp_now_send_status_t status) {
  if (status == ESP_NOW_SEND_SUCCESS) ++txOk;
  else ++txFailed;
  txBusy.store(false);
}

void onEspNow(const esp_now_recv_info_t *info, const uint8_t *data, int length) {
  if (!info || !info->src_addr || !info->des_addr || !data) return;
  if (memcmp(info->src_addr, kSenderMac, 6) || memcmp(info->des_addr, kReceiverMac, 6)) return;
  if (length == 4) ++ownEspNowPackets;
  else ++malformedOwn;
}

void onCsi(void *, wifi_csi_info_t *info) {
  if (!info) return;
  // Reject unrelated source/destination BEFORE retaining metadata or CSI.
  if (memcmp(info->mac, kSenderMac, 6) || memcmp(info->dmac, kReceiverMac, 6)) return;
  if (!info->buf || !info->len || info->len > kMaxCsiBytes || (info->len & 1) ||
      info->rx_ctrl.channel != CASCADIA_CHANNEL) {
    ++malformedOwn;
    return;
  }
  uint32_t sequence = 0;
  if (!decodeSequence(info->payload, info->payload_len, sequence)) {
    ++malformedOwn;
    return;
  }
  // Callback memory expires when it returns. Copy only bounded, owned data.
  CsiFrame frame{};
  frame.rx = info->rx_ctrl;
  frame.sequence = sequence;
  frame.length = info->len;
  frame.firstInvalid = info->first_word_invalid;
  memcpy(frame.mac, info->mac, sizeof(frame.mac));
  memcpy(frame.samples, info->buf, info->len);
  ++csiAccepted;
  if (xQueueSend(csiQueue, &frame, 0) != pdTRUE) ++queueDropped;
}

bool startRadio() {
  REQUIRE_OK(esp_netif_init());
  const esp_err_t events = esp_event_loop_create_default();
  if (events != ESP_OK && events != ESP_ERR_INVALID_STATE) {
    return requireOk(events, "esp_event_loop_create_default");
  }
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  cfg.csi_enable = 1;
  cfg.ampdu_tx_enable = 0;
  cfg.nvs_enable = 0;
  REQUIRE_OK(esp_wifi_init(&cfg));
  REQUIRE_OK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
  REQUIRE_OK(esp_wifi_set_mode(WIFI_MODE_STA));
  // Set MAC while STA is disabled, as required by the installed IDF API.
  REQUIRE_OK(esp_wifi_set_mac(WIFI_IF_STA, kLocalMac));
  REQUIRE_OK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20));
  REQUIRE_OK(esp_wifi_start());
  wifiStarted = true;
  REQUIRE_OK(esp_wifi_set_ps(WIFI_PS_NONE));
  REQUIRE_OK(esp_wifi_set_channel(CASCADIA_CHANNEL, WIFI_SECOND_CHAN_NONE));
  REQUIRE_OK(esp_wifi_set_promiscuous(false));
  REQUIRE_OK(esp_wifi_get_mac(WIFI_IF_STA, actualMac));
  if (memcmp(actualMac, kLocalMac, 6)) return requireOk(ESP_FAIL, "local MAC verification");
  uint8_t actualChannel = 0;
  wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
  REQUIRE_OK(esp_wifi_get_channel(&actualChannel, &secondary));
  if (actualChannel != CASCADIA_CHANNEL || secondary != WIFI_SECOND_CHAN_NONE) {
    return requireOk(ESP_FAIL, "fixed channel verification");
  }
  REQUIRE_OK(esp_now_init());
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, kPeerMac, 6);
  peer.channel = CASCADIA_CHANNEL;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  REQUIRE_OK(esp_now_add_peer(&peer));
  esp_now_rate_config_t rate{};
  rate.phymode = WIFI_PHY_MODE_HT20;
  rate.rate = WIFI_PHY_RATE_MCS0_LGI;
  rate.ersu = false;
  rate.dcm = false;
  REQUIRE_OK(esp_now_set_peer_rate_config(peer.peer_addr, &rate));
  if (kSender) {
    REQUIRE_OK(esp_now_register_send_cb(onSend));
  } else {
    csiQueue = xQueueCreate(kQueueDepth, sizeof(CsiFrame));
    if (!csiQueue) return requireOk(ESP_ERR_NO_MEM, "xQueueCreate(CSI)");
    REQUIRE_OK(esp_now_register_recv_cb(onEspNow));
    wifi_csi_config_t csi{};
    csi.lltf_en = true;
    csi.htltf_en = true;
    csi.stbc_htltf2_en = true;
    csi.ltf_merge_en = true;
    csi.channel_filter_en = true;
    csi.manu_scale = false;
    csi.shift = 0;
    csi.dump_ack_en = false;
    REQUIRE_OK(esp_wifi_set_csi_config(&csi));
    REQUIRE_OK(esp_wifi_set_csi_rx_cb(onCsi, nullptr));
    REQUIRE_OK(esp_wifi_set_csi(true));
  }
  return true;
}
#undef REQUIRE_OK

void printBanner() {
  Serial.printf("# CASCADIA_CSI role=%s profile=%s state=%s\n", kSender ? "sender" : "receiver", kProfile, ready ? "READY" : "ERROR");
  Serial.printf("# board=Arduino_Nano_ESP32_ABX00083 target=esp32s3 arduino=%s idf=%s usb=%s\n",
                ESP_ARDUINO_VERSION_STR, esp_get_idf_version(), ARDUINO_USB_MODE ? "HWCDC" : "TinyUSB_CDC");
  Serial.printf("# upstream=%s raw_signed_int8=1 gain_compensation=0 pairs=imaginary_then_real\n", kUpstreamRevision);
  Serial.printf("# own_mac=" MACSTR " expected_tx=" MACSTR " expected_rx=" MACSTR "\n",
                MAC2STR(actualMac), MAC2STR(kSenderMac), MAC2STR(kReceiverMac));
  Serial.printf("# channel=%d bandwidth=HT20 phy=MCS0_LGI send_hz=%d payload_bytes=4 unicast=1 promiscuous=0 scan=0 ap=0\n",
                CASCADIA_CHANNEL, CASCADIA_SEND_HZ);
  Serial.printf("# first_word=1_requires_host_to_discard_first_FOUR_scalars len_includes_them=1\n");
  if (failure) Serial.printf("# ERROR operation=%s code=%s radio_may_be_running=%d\n", failure, esp_err_to_name(failureCode), wifiStarted ? 1 : 0);
  if (!kSender) Serial.println(kCsvHeader);
}

void printStats() {
  Serial.printf("# CASCADIA_CSI role=%s profile=%s state=%s\n", kSender ? "sender" : "receiver", kProfile, ready ? "READY" : "ERROR");
  Serial.printf("# STATS uptime_ms=%" PRIu32 " own_espnow=%" PRIu32 " csi_accepted=%" PRIu32
                " malformed_own=%" PRIu32 " queue_drop=%" PRIu32 " serial_drop=%" PRIu32
                " rows=%" PRIu32 " tx_calls=%" PRIu32 " tx_error=%" PRIu32 " tx_ok=%" PRIu32
                " tx_fail=%" PRIu32 " tx_busy=%d\n", uint32_t(millis()), ownEspNowPackets.load(), csiAccepted.load(),
                malformedOwn.load(), queueDropped.load(), serialDropped, rowsWritten,
                txCalls.load(), txErrors.load(), txOk.load(), txFailed.load(), txBusy.load() ? 1 : 0);
}

void writeFrame(const CsiFrame &frame) {
  const auto &r = frame.rx;
  const int count = snprintf(csvLine, sizeof(csvLine),
      "CSI_DATA,%" PRIu32 "," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%" PRIu32 ",%d,%d,%d,%u,%d,\"[",
      frame.sequence, MAC2STR(frame.mac), int(r.rssi), int(r.rate), int(r.sig_mode), int(r.mcs),
      int(r.cwb), int(r.smoothing), int(r.not_sounding), int(r.aggregation), int(r.stbc),
      int(r.fec_coding), int(r.sgi), int(r.noise_floor), int(r.ampdu_cnt), int(r.channel),
      int(r.secondary_channel), uint32_t(r.timestamp), int(r.ant), int(r.sig_len), int(r.sig_mode),
      unsigned(frame.length), frame.firstInvalid ? 1 : 0);
  if (count < 0 || size_t(count) >= sizeof(csvLine)) { ++serialDropped; return; }
  size_t used = size_t(count);
  for (unsigned i = 0; i < frame.length; ++i) {
    const int n = snprintf(csvLine + used, sizeof(csvLine) - used, i ? ",%d" : "%d", int(frame.samples[i]));
    if (n < 0 || size_t(n) >= sizeof(csvLine) - used) { ++serialDropped; return; }
    used += size_t(n);
  }
  if (used + 3 >= sizeof(csvLine)) { ++serialDropped; return; }
  memcpy(csvLine + used, "]\"\n", 3);
  used += 3;
  const size_t written = Serial.write(reinterpret_cast<const uint8_t *>(csvLine), used);
  if (written != used) {
    ++serialDropped;
    // Delimit a truncated line; never claim it is a valid CSI sample.
    Serial.write('\n');
  } else ++rowsWritten;
}
}

void cascadiaSetup() {
#if !ARDUINO_USB_MODE
  // Keep read-side DTR/RTS changes and 1200-baud line coding from rebooting us.
  // Manual Nano double-tap recovery and DFU callbacks remain available.
  Serial.enableReboot(false);
#endif
  Serial.begin(115200);  // Native USB CDC, not a physical UART baud limit.
  Serial.setTxTimeoutMs(20);
  const uint32_t began = millis();
  while (!Serial && uint32_t(millis() - began) < 1500) delay(10);
  ready = startRadio();
  if (Serial) { printBanner(); consoleWasOpen = true; }
  lastStatsMs = millis();
  lastSendMs = millis();
}

void cascadiaLoop() {
  const bool consoleOpen = bool(Serial);
  const uint32_t now = millis();
  if (consoleOpen && !consoleWasOpen) printBanner();
  consoleWasOpen = consoleOpen;
  if (consoleOpen && uint32_t(now - lastStatsMs) >= 5000) {
    lastStatsMs = now;
    if (failure) printBanner();
    printStats();
  }
  if (!ready) { delay(20); return; }
  if (kSender) {
    const uint32_t intervalMs = 1000 / CASCADIA_SEND_HZ;
    if (!txBusy.load() && uint32_t(now - lastSendMs) >= intervalMs) {
      lastSendMs = now;  // No catch-up burst after a stall.
      uint8_t payload[4];
      encodeSequence(nextSequence++, payload);
      txBusy.store(true);
      ++txCalls;
      const esp_err_t result = esp_now_send(kReceiverMac, payload, sizeof(payload));
      if (result != ESP_OK) { ++txErrors; txBusy.store(false); }
    }
  } else if (xQueueReceive(csiQueue, &pendingFrame, 0) == pdTRUE) {
    if (consoleOpen) writeFrame(pendingFrame);
    else ++serialDropped;
  }
  delay(1);
}
