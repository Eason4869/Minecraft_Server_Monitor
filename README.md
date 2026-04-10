# AstrBot Minecraft 多服务器监控插件

这是一个面向 AstrBot 的 Minecraft 多服务器监控插件，支持同时监控多台 Java 版或 Bedrock 版服务器，并在状态变化时向指定 QQ 群发送通知。

插件重点能力：

- 支持在同一个插件实例中配置多台服务器
- 支持国内外双 API 查询源
- 默认使用 MineBBS MOTD API 作为国内源，并可通过 GUI 开关切换到 mcstatus.io
- 支持 Java / Bedrock 两种服务器类型
- 支持按服务器独立设置检测间隔
- 支持手动查询全部服务器状态
- 支持通过 GUI 开关控制消息展示项
- 仅在状态变化时推送通知，减少刷屏

## 致谢与来源

本插件参考了以下项目的设计与实现思路：

- [cryfly666/astrbot_plugin_apimcmc](https://github.com/cryfly666/astrbot_plugin_apimcmc)
在此基础上重点增强了多服务器监控、GUI 配置和消息展示项控制能力。

当前仓库地址：

- https://github.com/XiaolongYang-HZAU/minecraft_multi_monitor

## 适用场景

如果你有多台 Minecraft 服务器，希望统一在 AstrBot 中监控它们的在线状态、在线人数和基础运行信息，这个插件就是为这个场景设计的。

典型用法包括：

- 一个群内统一接收多个服的上下线通知
- 同时监控生存服、测试服、活动服
- 对 Java 服和 Bedrock 服进行混合监控

## 功能说明

### 1. 多服务器监控

插件会按配置列表逐台轮询服务器。每台服务器都可以有自己的：

- 唯一标识 `key`
- 显示名称 `server_name`
- 地址和端口
- 类型 `java` / `bedrock`
- 独立检测间隔
- 启用状态

### 2. 状态变化通知

监控任务会记录每台服务器最近一次状态，仅在以下情况发生时推送消息：

- 监控首次启动
- 服务器上线或离线
- 在线玩家人数变化
- 玩家加入或离开

### 3. GUI 友好的配置方式

多服务器配置已经调整为 GUI 可逐栏填写的结构。

### 4. 可配置的消息展示项

可以在插件配置页中通过开关控制以下内容是否显示：

- 服务器名称
- 服务器地址
- 服务器类型
- 游戏版本
- 在线玩家数量
- MOTD
- 当前在线玩家列表
- 更新时间
- 一言

默认关闭的项目：

- 服务器地址
- 游戏版本
- 当前在线玩家列表
- 一言

## 指令

- `/start_server_monitor` 启动监控任务
- `/stop_server_monitor` 停止监控任务
- `/查询` 立即查询当前所有已启用服务器的状态
- `/重置监控` 重置监控缓存

## 配置说明

插件通过 AstrBot 的 WebUI 配置，核心配置项如下。

### 全局配置

- `target_group`: 接收通知的 QQ 群号
- `check_interval`: 全局默认检测间隔
- `enable_auto_monitor`: 插件加载后是否自动启动监控
- `mcbbs_api`: 是否使用 MineBBS 作为默认查询源
- `display_options`: 消息展示项开关

### 服务器列表配置

`server_entries` 中的每一项代表一台服务器，支持以下字段：

- `key`: 唯一标识
- `enabled`: 是否启用
- `server_name`: 服务器名称
- `server_ip`: 服务器地址或域名
- `server_port`: 服务器端口
- `server_type`: `java` 或 `bedrock`
- `check_interval`: 当前服务器的检测间隔

## 工程结构

当前代码已按职责拆分为包结构：

- `main.py`: AstrBot 插件入口
- `minecraft_multi_monitor/plugin.py`: 插件主类与指令入口
- `minecraft_multi_monitor/config.py`: 配置解析与默认值处理
- `minecraft_multi_monitor/clients.py`: MineBBS / mcstatus 双 API 客户端
- `minecraft_multi_monitor/monitoring.py`: 消息格式化与状态变化检测
- `minecraft_multi_monitor/services.py`: 群消息发送与一言服务
- `minecraft_multi_monitor/models.py`: 数据模型

## 安装

将本插件目录放入 AstrBot 插件目录中，并确保 AstrBot 能读取以下文件：

- `main.py`
- `metadata.yaml`
- `_conf_schema.json`
- `README.md`

## 实现说明

插件支持两套查询源：

- 国内默认源：`https://motd.minebbs.com/api/status`
- 国际备用源：`https://api.mcstatus.io/v2/status`

默认启用 MineBBS 作为查询源，更适合中国大陆网络环境；如需切换到国际源，可在 GUI 中关闭 `mcbbs_api`。

一言内容来自：

- `https://v1.hitokoto.cn/`

如果关闭“一言”展示开关，插件将不会额外请求该接口。

## 注意事项

- 请确保机器人已加入目标 QQ 群且具备发言权限
- 请不要把检测间隔设置得过低，建议不低于 30 秒
- 若某台服务器检测间隔填写为 `0` 或负数，将回退到全局默认值
- 插件当前消息推送逻辑基于 QQ OneBot/AIOCQHTTP 适配器
