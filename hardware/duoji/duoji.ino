#include <WiFi.h>
#include <PubSubClient.h> 
#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h> 
#include <driver/i2s.h> // 使用内置硬件 I2S 驱动，不占运行内存，100% 拒绝 OOM 死机！

// 包含 MFRC522v2 库所需的头文件
#include <MFRC522v2.h>
#include <MFRC522DriverSPI.h>
#include <MFRC522DriverPinSimple.h>

// ====================== 1. 网络与 MQTT 云平台配置 ======================
const char *ssid = "zyyzyy";              
const char *password = "12345678zyyzyy";  

const char *mqtt_broker = "broker.emqx.io"; 
const int mqtt_port = 1883;
const char *topic_request = "community/gate/request";   
const char *topic_response = "community/gate/response"; 

// ====================== 2. 硬件引脚配置 ======================
#define SERVO 13       // 舵机
#define SS_PIN 5       // RC522 SDA (CS)
#define RST_PIN 4      // 💡 RFID RST 已挪到 GPIO 4 (D4)，完美避让 I2C 引脚！
#define FREQ 50        
#define RESOLUTION 10  

// 官方默认硬件 I2C 接口，兼容大屏与小屏 [5]
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define OLED_SDA      21  // OLED SDA 接 GPIO 21 [5]
#define OLED_SCL      22  // OLED SCK(SCL) 接 GPIO 22 [5]

// MAX98357A I2S 功放引脚 [2]
#define I2S_PORT      I2S_NUM_0
#define I2S_BCLK      14
#define I2S_LRC       27
#define I2S_DIN       16

// ====================== 3. 占空比常数定义 ======================
const int min_duty = 25;  // 开闸占空比
const int max_duty = 77; // 关闸占空比

// ====================== 4. 实例化外设与服务 ======================
WiFiClient espClient;
PubSubClient mqtt_client(espClient);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

MFRC522DriverPinSimple ss_pin_driver(SS_PIN); 
MFRC522DriverSPI driver{ss_pin_driver}; 
MFRC522 mfrc522{driver}; 

// ====================== 5. 屏幕显示函数（简洁大字版） ======================
void showIdleScreen() {
  display.clearDisplay();
  display.setTextSize(2); 
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(15, 10);
  display.println("ONLINE");
  display.setTextSize(1);
  display.setCursor(15, 40);
  display.println("Swipe Card...");
  display.display();
}

void showVerifyingScreen(String uid) {
  display.clearDisplay();
  display.setTextSize(2);
  display.setCursor(10, 10);
  display.println("CHECKING");
  display.setTextSize(1);
  display.setCursor(15, 40);
  display.println("UID: " + uid);
  display.display();
}

// ====================== 6. I2S 硬件音效驱动控制 ======================
void init_i2s_speaker() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 16000, 
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_MSB,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 64,
    .use_apll = false
  };
  
  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_BCLK,
    .ws_io_num = I2S_LRC,
    .data_out_num = I2S_DIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
}

// 播放电子正弦波提示音（不卡顿，不占内存，音质清脆）
void playChime(int frequency, int duration_ms) {
  int samples = 16000 * duration_ms / 1000;
  size_t bytes_written;
  
  for (int i = 0; i < samples; i++) {
    int16_t sample = 8000 * sin(2 * PI * frequency * i / 16000); // 8000为音量
    int16_t buffer[2] = { sample, sample }; 
    i2s_write(I2S_PORT, &buffer, sizeof(buffer), &bytes_written, portMAX_DELAY);
  }
}

// ====================== 7. 网络连接服务函数 ======================
void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected!");
}

void reconnect() {
  while (!mqtt_client.connected()) {
    Serial.print("Attempting MQTT connection...");
    String clientId = "ESP32Gate-";
    clientId += String(random(0xffff), HEX); 
    
    if (mqtt_client.connect(clientId.c_str())) {
      Serial.println("connected to MQTT!");
      mqtt_client.subscribe(topic_response); 
    } else {
      delay(5000);
    }
  }
}

// ====================== 8. 核心：云端控制指令下发与硬件联动回调 ======================
void callback(char *topic, byte *payload, unsigned int length) {
  Serial.print("收到云端下行数据: ");
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println(message);

  // 1. 验证通过 (APPROVED)
  if (message.indexOf("APPROVED") != -1) {
    Serial.println(F("✅ 验证成功！正在开闸..."));
    
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(40, 10);
    display.println("PASS"); 
    display.setTextSize(1);
    display.setCursor(15, 40);
    display.println("Welcome Home!");
    display.display();

    // 播放“欢快开闸”提示音（高音滴 2 声）
    playChime(1500, 80);  
    delay(40);
    playChime(1800, 150); 

    // 舵机开闸
    ledcWrite(SERVO, min_duty); 
    delay(3000); // 闸门保持开闸 3 秒
    
    // 舵机关闸
    Serial.println(F("正在关闭道闸门..."));
    ledcWrite(SERVO, max_duty); 
    showIdleScreen(); 
  } 
  // 2. 验证失败 (DENIED)
  else if (message.indexOf("DENIED") != -1) {
    Serial.println(F("❌ 验证失败：拒绝通行！"));
    
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(25, 10);
    display.println("DENIED"); 
    display.setTextSize(1);
    display.setCursor(15, 40);
    
    if (message.indexOf("Expired") != -1) {
      display.println("Expired Card");
    } else if (message.indexOf("Revoked") != -1) {
      display.println("Lost Card");
    } else {
      display.println("Invalid Card");
    }
    display.display();

    // 播放“警告拦截”提示音（低音嘟 1 长声）
    playChime(350, 450); 

    ledcWrite(SERVO, max_duty); // 保持闸门关闭
    delay(3000);
    showIdleScreen(); 
  }
}

// ====================== 9. 初始化配置 ======================
void setup() {
  Serial.begin(115200);

  // 初始化大屏幕 I2C 
  Wire.begin(OLED_SDA, OLED_SCL);
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) { // 扫描出的地址是0x3C
    Serial.println(F("OLED failed"));
  } else {
    display.clearDisplay();
    display.display();
  }

  // 1. 初始化硬件 I2S 驱动 [2]
  init_i2s_speaker();

  // 2. 连接 WiFi 与 MQTT
  setup_wifi();
  mqtt_client.setServer(mqtt_broker, mqtt_port);
  mqtt_client.setCallback(callback);

  // 3. 初始化 SPI 和 MFRC522 (注意：RST 已经移到引脚 4)
  SPI.begin(18, 19, 23, SS_PIN); 
  pinMode(RST_PIN, OUTPUT); 
  digitalWrite(RST_PIN, LOW);  
  delay(50);                   
  digitalWrite(RST_PIN, HIGH); 
  delay(50);                   

  mfrc522.PCD_Init(); 
  Serial.println(F("MFRC522模块已初始化。"));

  // 4. 配置并绑定舵机
  if (ledcAttach(SERVO, FREQ, RESOLUTION)) {
    ledcWrite(SERVO, max_duty); // 初始大闸关闭
  }

  // 播放开机清脆滴声
  playChime(1000, 100);

  showIdleScreen();
}

// ====================== 10. 主循环逻辑 ======================
void loop() {
  // 维护网络
  if (!mqtt_client.connected()) {
    reconnect();
  }
  mqtt_client.loop(); 

  // 检查读卡
  if (!mfrc522.PICC_IsNewCardPresent() || !mfrc522.PICC_ReadCardSerial()) {
    return; 
  }

  // 读取并格式化卡号
  String cardUID = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    cardUID += String(mfrc522.uid.uidByte[i] < 0x10 ? "0" : "");
    cardUID += String(mfrc522.uid.uidByte[i], HEX);
  }
  cardUID.toUpperCase(); 

  Serial.println("读取卡号: " + cardUID);

  // 屏幕提示：CHECKING
  showVerifyingScreen(cardUID);

  // 打包发送
  String jsonPayload = "{\"device_id\":\"ESP32_Gate_01\",\"card_uid\":\"" + cardUID + "\"}";
  mqtt_client.publish(topic_request, jsonPayload.c_str());

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1(); 

  delay(1500); 
}