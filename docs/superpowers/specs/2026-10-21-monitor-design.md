# Minecraft 监控增强设计

用户已明确授权按前轮审查建议直接实施，先修复问题，再加入分群设置与图片渲染；不再设置额外审批关卡。

## 范围

- 保留双 API、Java/Bedrock、原展示开关和旧命令；修复重复 key、版本字段、名单含义、通知丢失、权限和任务回收。
- 服务器统一定义，完整 UMO 标识群订阅。普通用户只能查询本群订阅；配置修改和全局操作仅 AstrBot 管理员可用。
- 群订阅通过 WebUI 或 /mc 管理，并写入 AstrBotConfig.save_config()；保存失败回滚。
- 默认查询发图片、通知发文字；图片可选，渲染失败回退文字，每页最多四台服务器。
- 复用 AstrBot html_render 和 Context.send_message，不新增服务或 Docker。

## 数据与流程

- group_entries: session、server_keys、default_server、query_style、notification_style、notify_mode(all/status/off)。空订阅不授予访问权。
- 同服请求锁与短时缓存合并重复请求；HTTP Session 复用，采集有限并发。
- 检测状态与投递状态分离。按 UMO+server key 维护待发最新通知，失败最多三次退避重试，五分钟过期，逐群隔离。
- 离线连续两次确认；API 连续失败进入 unknown，不能伪装成服务器离线。名字默认作为样本，不宣称可靠的完整进出事件。
- 延迟启动、监控、投递、HTTP 资源均归插件生命周期管理；reset 使用既有锁同步。
- 旧 target_group 仅在唯一 OneBot 实例可确定时自动迁移；多实例必须明确绑定，防止误投。

## 验收

pytest 覆盖纯逻辑与带 AstrBot 边界替身的完整指令、生命周期、并发、失败重试、权限隔离；本地 HTTP 测试验证实际 aiohttp 行为。Ruff、编译、schema 检查及图片预览通过后打包。真实 AstrBot/QQ 发送另列部署验证边界。
