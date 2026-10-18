/*
 * csi_tx: CSIセンシング用の送信機。
 *
 * WiFiには接続せず、固定チャンネルでESP-NOWのブロードキャストフレームを一定間隔で送信するだけ。
 * CSIは受信側（csi_rx）のPHYがこのフレームから取得する。
 * STAのMACアドレスを 02:C5:15:00:00:<TX ID> に設定し、csi_rxが送信元MACの末尾で個体を識別する。
 */
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "csi_tx";

/* csi_rx側の識別用MACプレフィックスと一致させること（docs/design_tx_rx.md） */
static const uint8_t TX_MAC_PREFIX[5] = {0x02, 0xC5, 0x15, 0x00, 0x00};
static const uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    uint8_t mac[6];
    memcpy(mac, TX_MAC_PREFIX, sizeof(TX_MAC_PREFIX));
    mac[5] = (uint8_t)CONFIG_CSI_TX_ID;
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, mac)); /* esp_wifi_start()より前に設定する */

    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_CSI_TX_CHANNEL, WIFI_SECOND_CHAN_NONE));

    ESP_LOGI(TAG, "mac=%02x:%02x:%02x:%02x:%02x:%02x channel=%d",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], CONFIG_CSI_TX_CHANNEL);
}

static void espnow_init(void)
{
    ESP_ERROR_CHECK(esp_now_init());

    esp_now_peer_info_t peer = {
        .channel = CONFIG_CSI_TX_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, BROADCAST_MAC, sizeof(BROADCAST_MAC));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /* 低い固定レート（HT20 MCS0）で送る。受信側のCSI取得が安定する */
    esp_now_rate_config_t rate_config = {
        .phymode = WIFI_PHY_MODE_HT20,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false,
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(BROADCAST_MAC, &rate_config));
}

static void send_task(void *pvParameters)
{
    struct {
        char magic[4];
        uint8_t tx_id;
        uint32_t seq;
    } __attribute__((packed)) payload = {
        .magic = "CSIT",
        .tx_id = (uint8_t)CONFIG_CSI_TX_ID,
        .seq = 0,
    };

    uint32_t fail_count = 0;
    TickType_t last_wake = xTaskGetTickCount();
    while (1) {
        payload.seq++;
        esp_err_t err = esp_now_send(BROADCAST_MAC, (const uint8_t *)&payload, sizeof(payload));
        if (err != ESP_OK && (++fail_count % 100) == 1) {
            ESP_LOGW(TAG, "esp_now_send failed: %s (count=%u)", esp_err_to_name(err), (unsigned)fail_count);
        }
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(CONFIG_CSI_TX_INTERVAL_MS));
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init();
    espnow_init();

    xTaskCreate(send_task, "send_task", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "tx_id=%d sending every %dms", CONFIG_CSI_TX_ID, CONFIG_CSI_TX_INTERVAL_MS);
}
