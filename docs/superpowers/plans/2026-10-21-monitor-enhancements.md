# Minecraft Monitor Implementation Plan

**Goal:** 修复监控可靠性并交付分群订阅和图片查询。
**Architecture:** 保持 config/models/clients/monitoring/services 边界，增加 subscriptions、delivery、rendering 模块，入口只负责协调。
**Tech Stack:** Python >=3.10、aiohttp、AstrBot >=4.10.4、HTML/Jinja2；开发使用 pytest、Ruff。
**Spec:** ../specs/2026-10-21-monitor-design.md

## 执行

当前会话直接执行。新克隆隔离目录、功能分支，无用户已有修改。不推送、不发布。

## 任务

- [x] 1. 先写回归测试，确认重复 key、名单、离线、版本字段失败；修复配置和状态比较。
- [x] 2. HTTP Session 复用、有限并发、重复请求缓存、unknown 状态；本地 HTTP 与异常测试。
- [x] 3. 先测试分群隔离、配置保存回滚和投递重试；实现 subscriptions/delivery/services。
- [x] 4. 先测试权限、任务回收、分页与渲染降级；接入 /mc、旧命令和 HTML 卡片。
- [x] 5. 更新 WebUI schema、README、CHANGELOG；完整回归、独立审查、截图和 ZIP 校验。

## 重点检查

跨平台同群号不串群；未授权用户无法订阅未绑定服务器；名单为空不代表无人；一个群失败不重发成功群；卸载后无后台任务；渲染异常不阻断文字。

## 执行记录

44 项回归测试通过；Ruff 与编译通过；Edge 实际模板截图验证通过。独立审查发现并修复稳定 key、禁用绑定和 reset/stop 竞态，复核无剩余 P1/P2。额外保存 WebUI template_list 标识并覆盖测试。实际 AstrBot/QQ 联调尚未执行。
