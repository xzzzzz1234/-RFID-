#include <WiFi.h>

const char *ssid = "zyyzyy"; //你的网络名称
const char *password = "12345678zyyzyy"; //你的网络密码

void setup()
{
  Serial.begin(115200);
  Serial.println();

  WiFi.begin(ssid, password); //连接网络

  while (WiFi.status() != WL_CONNECTED) //等待网络连接成功
  {
    delay(500);
    Serial.print(".");
  }
  Serial.println("WiFi connected!");

  Serial.println("IP address: ");
  Serial.println(WiFi.localIP()); //打印模块IP
}

void loop()
{
}
