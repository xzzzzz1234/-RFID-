import sqlite3
import json
from datetime import datetime
import paho.mqtt.client as mqtt

# ----------------- 1. 初始化 SQLite 数据库并绑定 5 张卡 -----------------
def init_db():
    conn = sqlite3.connect("gate_system.db")
    cursor = conn.cursor()
    # 创建车辆信息表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Vehicles (
            plate_number TEXT PRIMARY KEY,
            owner_name TEXT,
            card_uid TEXT UNIQUE,
            status INTEGER,       -- 1: 正常通行, 0: 卡片挂失/禁用
            expire_date TEXT       -- 格式: YYYY-MM-DD
        )
    ''')
    
    # 动态绑定您刚才获取的 5 张卡片（去除了空格并统一转为大写）
    initial_cars = [
        ("浙A·88888", "张三", "F1CD2203", 1, "2028-12-31"), # 正常月租车
        ("沪A·66666", "李四", "AF786BDB", 1, "2028-12-31"), # 正常月租车
        ("京A·00001", "王五", "7FE0C3DB", 1, "2028-12-31"), # 正常月租车
        ("粤B·12345", "赵六", "9F7865DB", 0, "2028-12-31"), # 模拟：卡片已挂失
        ("苏E·54321", "钱七", "EF27A2DB", 1, "2025-01-01")  # 模拟：卡片已超期（2025年到期，当前2026年）
    ]
    
    try:
        cursor.executemany("INSERT INTO Vehicles VALUES (?, ?, ?, ?, ?)", initial_cars)
        conn.commit()
        print("🎉 5张卡片数据已成功录入数据库！")
    except sqlite3.IntegrityError:
        print("💡 数据库已存在卡片记录，读取现有配置。")
    conn.close()

# ----------------- 2. MQTT 云平台回调逻辑 -----------------
def on_connect(client, userdata, flags, rc):
    print(f"已成功连接到 EMQX 物联网云平台！返回码: {rc}")
    # 订阅设备端的刷卡请求主题
    client.subscribe("community/gate/request")
    print("已订阅设备端刷卡主题: community/gate/request")

def on_message(client, userdata, msg):
    try:
        # 解析来自 ESP32S3 上报的 JSON 数据
        data = json.loads(msg.payload.decode())
        raw_uid = data.get("card_uid", "")
        # 统一格式化：去除空格并转大写（例如 "F1 CD 22 03" -> "F1CD2203"）
        card_uid = raw_uid.replace(" ", "").upper()
        print(f"\n[云端消息] 收到车辆刷卡请求 -> 物理卡号: {card_uid}")

        # 查询本地数据库
        conn = sqlite3.connect("gate_system.db")
        cursor = conn.cursor()
        cursor.execute("SELECT owner_name, plate_number, status, expire_date FROM Vehicles WHERE card_uid=?", (card_uid,))
        result = cursor.fetchone()
        conn.close()

        # 默认回执：查无此车
        response = {
            "status": "DENIED",
            "owner": "Unknown",
            "plate": "None",
            "msg": "Card Not Found"
        }

        if result:
            owner, plate, status, expire_date = result
            # 校验是否过期
            is_expired = datetime.strptime(expire_date, "%Y-%m-%d") < datetime.now()

            if status == 0:
                response = {"status": "DENIED", "owner": owner, "plate": plate, "msg": "Card Revoked"}
                print(f"❌ 警告：车主【{owner}】的卡片已挂失，拒绝通行！")
            elif is_expired:
                response = {"status": "DENIED", "owner": owner, "plate": plate, "msg": "Card Expired"}
                print(f"❌ 警告：车主【{owner}】的月租卡已超期({expire_date})，拒绝通行！")
            else:
                response = {"status": "APPROVED", "owner": owner, "plate": plate, "msg": "Welcome Home"}
                print(f"✅ 放行：车主【{owner}】(车牌:{plate}) 验证通过！")
        else:
            print(f"❌ 拦截：卡号【{card_uid}】未登记，属于外来车辆！")

        # 将处理结果打包成 JSON，发布到下行控制主题，通知 ESP32S3
        client.publish("community/gate/response", json.dumps(response))
        print(f"[指令下发] 结果已通过云平台推送给 ESP32S3: {response}")

    except Exception as e:
        print(f"处理消息时发生错误: {e}")

# ----------------- 3. 主程序启动 -----------------
if __name__ == "__main__":
    init_db()
    
    # 建立 MQTT 客户端
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    client.on_connect = on_connect
    client.on_message = on_message

    # 连接 EMQX 公共服务器
    client.connect("broker.emqx.io", 1883, 60)
    
    # 阻塞式循环监听云端数据
    print("物联网 Python 业务中控已启动，等待刷卡数据...")
    client.loop_forever()