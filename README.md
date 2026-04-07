# AstrBot Minecraft 多服务器监控插件

这是一个面向 AstrBot 的 Minecraft 多服务器监控插件，支持同时监控多台 Java 版或 Bedrock 版服务器，并在状态变化时向指定 QQ 群发送通知。

插件重点能力：

- 支持在同一个插件实例中配置多台服务器
- 支持 Java / Bedrock 两种服务器类型
- 支持按服务器独立设置检测间隔
- 支持手动查询全部服务器状态
- 支持通过 GUI 开关控制消息展示项
- 仅在状态变化时推送通知，减少刷屏

## 致谢与来源

本插件参考了以下项目的设计与实现思路：

- [cryfly666/astrbot_plugin_apimcmc](https://github.com/cryfly666/astrbot_plugin_apimcmc)
在此插件基础上重点提升了多服务器监控能力，并允许使用者自由开关要发送的每个条目。
![](https://picgo-temporary-storage-yxl.oss-cn-hangzhou.aliyuncs.com/Yxl-Picgo-temporary/%E5%B1%8F%E5%B9%95%E6%88%AA%E5%9B%BE%202026-04-07%20155043.png)
![](https://picgo-temporary-storage-yxl.oss-cn-hangzhou.aliyuncs.com/Yxl-Picgo-temporary/20260407155200.png)
本仓库在整理、重构和文档编写过程中使用了 Codex 协助开发。

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

## 安装

将本插件目录放入 AstrBot 插件目录中，并确保 AstrBot 能读取以下文件：

- `main.py`
- `metadata.yaml`
- `_conf_schema.json`
- `README.md`

## 开发环境

当前工作区已创建 conda 开发环境：

```bash
conda activate astrbot_mc_monitor_dev
```

环境中已安装：

- Python 3.11
- aiohttp

## 实现说明

插件通过 `mcstatus.io` API 获取服务器状态，并使用 AstrBot 提供的平台接口向指定群发送消息。

一言内容来自：

- `https://v1.hitokoto.cn/`

如果关闭“一言”展示开关，插件将不会额外请求该接口。

## 注意事项

- 请确保机器人已加入目标 QQ 群且具备发言权限
- 请不要把检测间隔设置得过低，建议不低于 30 秒
- 若某台服务器未填写独立检测间隔，将回退到全局默认值
- 发布到 GitHub 前，请在 `metadata.yaml` 中补充最终仓库地址
