# 项目架构文档

## 一、系统总体架构

本系统采用四层架构设计，各层职责清晰、耦合度低。

```
┌─────────────────────────────────────────────┐
│            Web 管理客户端（浏览器）             │
│  登录 / 仪表盘 / 查询 / 添加 / 修改 / 删除     │
└────────────────────┬────────────────────────┘
                     │ HTTP
┌────────────────────▼────────────────────────┐
│            Flask 后端服务 (app.py)             │
│  路由处理 / 权限控制 / 业务逻辑 / MQTT 后台线程  │
└────────────────────┬────────────────────────┘
                     │ SQL
┌────────────────────▼────────────────────────┐
│          SQLite 本地数据库 (gate_system.db)    │
│  Users / Owners / Vehicles / AccessLogs       │
└────────────────────┬────────────────────────┘
                     │ MQTT (broker.emqx.io)
┌────────────────────▼────────────────────────┐
│         ESP32 硬件端 (Arduino)                │
│  RC522 RFID / SG90 舵机 / SH1106 OLED        │
└─────────────────────────────────────────────┘
```

## 二、通信架构

### MQTT 主题设计

| 主题 | 方向 | 说明 |
|------|------|------|
| `community/gate/request` | ESP32 → 后台 | 刷卡请求，上报卡片 UID |
| `community/gate/response` | 后台 → ESP32 | 通行结果，下发允许/拒绝 |

### 消息格式

上行请求（ESP32 发送）：

```json
{
  "card_uid": "F1 CD 22 03"
}
```

下行响应（后台发送）：

```json
{
  "status": "APPROVED",
  "owner": "张三",
  "plate": "浙A·88888",
  "msg": "Welcome Home"
}
```

status 取值：`APPROVED`（放行）或 `DENIED`（拒绝）

## 三、数据库设计

### ER 关系

```
Users（管理员）
  ↓ 登录系统

Owners（车主）1 ←── N Vehicles（车辆）
                              ↓ 刷卡触发
                        AccessLogs（出入记录）
```

### 表结构

#### Users 表

| 字段 | 类型 | 说明 |
|------|------|------|
| user_id | INTEGER PK | 自增主键 |
| username | TEXT UNIQUE | 用户名 |
| password | TEXT | 哈希密码 |
| created_at | TEXT | 创建时间 |

#### Owners 表

| 字段 | 类型 | 说明 |
|------|------|------|
| owner_id | INTEGER PK | 自增主键 |
| owner_name | TEXT | 车主姓名 |
| phone | TEXT | 联系电话 |
| address | TEXT | 住址 |
| remark | TEXT | 备注 |
| created_at | TEXT | 创建时间 |

#### Vehicles 表

| 字段 | 类型 | 说明 |
|------|------|------|
| vehicle_id | INTEGER PK | 自增主键 |
| plate_number | TEXT UNIQUE | 车牌号 |
| owner_id | INTEGER FK | 关联车主 |
| card_uid | TEXT UNIQUE | RFID 卡 UID |
| vehicle_type | TEXT | 车辆类型 |
| status | INTEGER | 1=正常, 0=挂失 |
| expire_date | TEXT | 到期日期 |
| created_at | TEXT | 创建时间 |

#### AccessLogs 表

| 字段 | 类型 | 说明 |
|------|------|------|
| log_id | INTEGER PK | 自增主键 |
| card_uid | TEXT | 刷卡 UID |
| plate_number | TEXT | 车牌号 |
| owner_name | TEXT | 车主姓名 |
| access_type | TEXT | 入口/出口 |
| access_result | TEXT | APPROVED/DENIED |
| reason | TEXT | 结果原因 |
| access_time | TEXT | 刷卡时间 |

## 四、后端架构

### 程序入口

`app.py` 是整个软件端的唯一入口，启动时执行：

1. `init_db()` — 初始化/迁移数据库，创建表和种子数据
2. `start_mqtt()` — 后台守护线程，连接 EMQX 并订阅刷卡主题
3. `app.run()` — 启动 Flask Web 服务

### 核心模块划分

| 模块 | 职责 |
|------|------|
| 数据库层 | `db()`, `one()`, `all_rows()`, `exec_sql()` 封装 SQLite 操作 |
| 业务逻辑层 | `judge_card()` 判断通行权限, `log_access()` 保存出入记录 |
| MQTT 通信层 | `on_connect`, `on_message`, `start_mqtt()` 管理物联网通信 |
| Web 路由层 | Flask 路由，处理 HTTP 请求并渲染页面 |
| 权限控制 | `login_required` 装饰器，Session 管理 |

### 刷卡判断逻辑

```
judge_card(card_uid)
    ├── 查询 Vehicles 表（JOIN Owners）
    ├── 未找到 → DENIED: Card Not Found
    ├── status == 0 → DENIED: Card Revoked
    ├── expire_date < 当前日期 → DENIED: Card Expired
    └── 通过校验 → APPROVED: Welcome Home
    （每个分支都会写入 AccessLogs）
```

## 五、前端架构

### 模板继承

```
base.html（公共布局：侧边栏 + 顶栏 + 消息提示）
    ├── dashboard.html    首页仪表盘
    ├── owners.html       车主列表
    ├── owner_form.html   车主表单
    ├── vehicles.html     车辆列表
    ├── vehicle_form.html 车辆表单
    ├── access_logs.html  出入记录列表
    ├── access_log_form.html 出入记录表单
    ├── search.html       综合查询
    └── password.html     修改密码

login.html（独立页面，不继承 base.html）
```

### 静态资源

| 文件 | 作用 |
|------|------|
| `static/css/style.css` | 全局样式，包含布局、组件、响应式适配 |
| `static/js/main.js` | 删除确认弹窗等前端交互 |

### UI 设计规范

- 配色：蓝白渐变主色调
- 布局：左侧菜单栏 + 右侧内容区
- 组件：圆角卡片、分离式表格行、渐变按钮、状态标签
- 响应式：支持 1180px / 860px 断点适配

## 六、硬件端架构

### 引脚分配

| 外设 | 引脚 |
|------|------|
| RC522 RFID (SPI) | MOSI=23, MISO=19, SCK=18, SDA=5, RST=4 |
| SH1106 OLED (I2C) | SDA=21, SCL=22 |
| SG90 舵机 (PWM) | GPIO 13 |

### 硬件端工作流

```
loop()
    ├── 检测是否有新卡片
    ├── 读取 UID → 格式化为十六进制字符串
    ├── 构建 JSON → 发布到 community/gate/request
    ├── 等待 community/gate/response 回调
    ├── 解析 status 字段
    ├── OLED 显示结果（msg 字段）
    ├── APPROVED → 舵机旋转 90° → 延时 3s → 复位
    └── DENIED → 不开闸
```

## 七、安全设计

| 方面 | 措施 |
|------|------|
| 密码存储 | Werkzeug 哈希加密，数据库中不存明文 |
| 登录保护 | Flask Session + `login_required` 装饰器 |
| SQL 注入防护 | 全部使用参数化查询 |
| UID 格式化 | 统一去空格转大写，防止格式差异绕过 |
| 删除保护 | 删除车主前检查是否有关联车辆 |
| 前端确认 | 删除操作弹出确认对话框 |

## 八、部署说明

本系统为课设演示项目，设计为单机本地运行：

- Flask 运行在 `127.0.0.1:5000`
- SQLite 数据库存储在 `gate_system.db` 文件
- MQTT 通过互联网连接 `broker.emqx.io` 公共服务
- 硬件端通过 WiFi 接入同一 MQTT Broker
- 无需外部数据库服务、无需 Docker、无需云服务器
