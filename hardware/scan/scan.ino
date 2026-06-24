#include <Wire.h>

void setup() {
  Serial.begin(115200);
  while (!Serial); 
  
  Serial.println("\n=== 默认硬件 I2C 引脚扫描开始 ===");
  // 不传递任何引脚参数，Arduino 会自动强制启用 ESP32 默认物理硬件引脚：SDA=21, SCL=22 [5]
  Wire.begin(); 
}

void loop() {
  byte error, address;
  int nDevices;

  Serial.println("正在扫描默认 I2C 端口 (SDA=21, SCL=22)...");

  nDevices = 0;
  for (address = 1; address < 127; address++) {
    // 向该地址发送握手信号
    Wire.beginTransmission(address);
    error = Wire.endTransmission();

    if (error == 0) {
      Serial.print("发现 I2C 设备！物理地址为: 0x");
      if (address < 16) Serial.print("0");
      Serial.print(address, HEX);
      Serial.println("  !");
      nDevices++;
    } 
    else if (error == 4) {
      Serial.print("在地址 0x");
      if (address < 16) Serial.print("0");
      Serial.print(address, HEX);
      Serial.println(" 发生未知错误");
    }
  }

  if (nDevices == 0) {
    Serial.println("❌ 默认引脚未扫描到设备。请确保：\n1. 屏幕头部4个针脚已经锡焊\n2. 尝试将 SDA(21) 和 SCK(22) 线互换对调\n3. 电源已接通\n");
  } else {
    Serial.println("扫描结束。\n");
  }

  delay(3000); // 每3秒扫描一次
}