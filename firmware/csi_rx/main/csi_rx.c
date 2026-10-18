/*
 * csi_rx: CSIセンシング用の受信機（1台、PCにUSB接続）。
 *
 * 固定チャンネルでcsi_txのESP-NOWフレームを受信し、フレームごとのCSIを取得する。
 * 送信元MAC（02:C5:15:00:00:<TX ID>）からTX IDを決め、docs/design_tx_rx.md のシリアルフレームとして
 * UART0（または USB-Serial-JTAG）へ出力する。フレームの中身は docs/protocol.md のパケットと同一。
 *
 *   [sync 0xAA 0x55][len u16][payload: header14 + csi][crc16(payload) u16]   すべてリトルエンディアン
 */
#include <string.h>

#include "driver/uart.h"
#if CONFIG_CSI_RX_USE_USB_SERIAL_JTAG
#include "driver/usb_serial_jtag.h"
#endif
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "csi_rx";

#define CSI_MAX_BYTES 256
#define FRAME_QUEUE_LEN 64
#define SYNC0 0xAA
#define SYNC1 0x55

/* csi_tx側のMACプレフィックスと一致させること */
static const uint8_t TX_MAC_PREFIX[5] = {0x02, 0xC5, 0x15, 0x00, 0x00};

/* docs/protocol.md のパケットヘッダ（サーバーのcsi_receiver.packetと一致させること） */
typedef struct __attribute__((packed)) {
    uint8_t magic;         /* 0xC5 固定 */
    uint8_t sensor_id;     /* = TX ID */
    uint16_t seq;          /* TXごとの受信連番 */
    uint32_t timestamp_us; /* esp_timer_get_time()の下位32bit（CSI受信時刻） */
    int8_t rssi;
    uint8_t rate;
    uint8_t reserved[2];
    uint16_t csi_len;
} csi_packet_header_t;

#define PAYLOAD_MAX (sizeof(csi_packet_header_t) + CSI_MAX_BYTES)
#define FRAME_MAX (2 + 2 + PAYLOAD_MAX + 2)

typedef struct {
    uint16_t len;
    uint8_t data[FRAME_MAX];
} frame_item_t;

static QueueHandle_t s_frame_queue;
static uint16_t s_seq[256];
static volatile uint32_t s_rx_count[256];
static volatile uint32_t s_dropped;

/* CRC-16/CCITT-FALSE（poly 0x1021, init 0xFFFF）。Pythonの binascii.crc_hqx(data, 0xFFFF) と一致 */
static uint16_t crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    while (len--) {
        crc ^= (uint16_t)(*data++) << 8;
        for (int i = 0; i < 8; i++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (info == NULL || info->buf == NULL || s_frame_queue == NULL) {
        return;
    }
    /* csi_tx以外（近隣のWiFi機器など）のフレームは捨てる */
    if (memcmp(info->mac, TX_MAC_PREFIX, sizeof(TX_MAC_PREFIX)) != 0) {
        return;
    }
    uint8_t tx_id = info->mac[5];
    uint16_t csi_len = info->len > CSI_MAX_BYTES ? CSI_MAX_BYTES : info->len;

    frame_item_t item;
    uint8_t *p = item.data;
    uint16_t payload_len = (uint16_t)(sizeof(csi_packet_header_t) + csi_len);

    *p++ = SYNC0;
    *p++ = SYNC1;
    *p++ = (uint8_t)(payload_len & 0xFF);
    *p++ = (uint8_t)(payload_len >> 8);

    uint8_t *payload = p;
    csi_packet_header_t header = {
        .magic = 0xC5,
        .sensor_id = tx_id,
        .seq = s_seq[tx_id]++,
        .timestamp_us = (uint32_t)esp_timer_get_time(),
        .rssi = info->rx_ctrl.rssi,
        .rate = info->rx_ctrl.rate,
        .reserved = {0, 0},
        .csi_len = csi_len,
    };
    memcpy(p, &header, sizeof(header));
    p += sizeof(header);
    memcpy(p, info->buf, csi_len);
    p += csi_len;

    uint16_t crc = crc16_ccitt(payload, payload_len);
    *p++ = (uint8_t)(crc & 0xFF);
    *p++ = (uint8_t)(crc >> 8);
    item.len = (uint16_t)(p - item.data);

    s_rx_count[tx_id]++;
    /* キュー満杯時は古いデータの送信を待たず、このフレームを捨てる */
    if (xQueueSend(s_frame_queue, &item, 0) != pdTRUE) {
        s_dropped++;
    }
}

static void espnow_recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    /* CSIはPHY層で取得するため、ESP-NOWのペイロードは使わない（受信有効化のためだけに登録） */
}

static void serial_init(void)
{
#if CONFIG_CSI_RX_USE_USB_SERIAL_JTAG
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.tx_buffer_size = 8192;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    ESP_LOGI(TAG, "csi frames -> native USB (USB-Serial-JTAG), logs stay on UART0");
#else
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 256, 8192, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_set_baudrate(UART_NUM_0, CONFIG_CSI_RX_BAUD));
    /* UART0はCSIフレーム専用にする。ログがバイナリに混ざるのを減らす（混ざってもPC側がCRCで除外する） */
    esp_log_level_set("*", ESP_LOG_ERROR);
#endif
}

static void serial_write(const uint8_t *data, size_t len)
{
#if CONFIG_CSI_RX_USE_USB_SERIAL_JTAG
    usb_serial_jtag_write_bytes(data, len, pdMS_TO_TICKS(20));
#else
    uart_write_bytes(UART_NUM_0, data, len);
#endif
}

static void serial_sender_task(void *pvParameters)
{
    frame_item_t item;
    while (1) {
        if (xQueueReceive(s_frame_queue, &item, portMAX_DELAY) == pdTRUE) {
            serial_write(item.data, item.len);
        }
    }
}

static void stats_task(void *pvParameters)
{
    uint32_t last[256] = {0};
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        for (int id = 0; id < 256; id++) {
            uint32_t now = s_rx_count[id];
            if (now != last[id]) {
                ESP_LOGI(TAG, "tx_id=%d: %u frames/5s", id, (unsigned)(now - last[id]));
                last[id] = now;
            }
        }
        if (s_dropped) {
            ESP_LOGW(TAG, "dropped (queue full): %u", (unsigned)s_dropped);
        }
    }
}

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_CSI_RX_CHANNEL, WIFI_SECOND_CHAN_NONE));
}

static void csi_init(void)
{
    /* L-LTFのみ（64サブキャリア = 128バイト/フレーム）。サーバー側の想定長と帯域の見積もりに合わせる */
    wifi_csi_config_t csi_config = {
        .lltf_en = true,
        .htltf_en = false,
        .stbc_htltf2_en = false,
        .ltf_merge_en = true,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
        .dump_ack_en = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(&wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    s_frame_queue = xQueueCreate(FRAME_QUEUE_LEN, sizeof(frame_item_t));

    wifi_init();
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_recv_cb));
    csi_init();

    serial_init();
    xTaskCreate(serial_sender_task, "serial_sender", 4096, NULL, 5, NULL);
    xTaskCreate(stats_task, "stats", 3072, NULL, 1, NULL);
    ESP_LOGI(TAG, "ready: channel=%d", CONFIG_CSI_RX_CHANNEL);
}
