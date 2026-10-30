# Qoder Sites 发布记录 — 研读台 ReadTrace

## 站点信息

| 字段 | 值 |
|------|-----|
| 站点 URL | https://readtrace-research-edswj48b142.qoder.zone |
| 站点名称 (slug) | readtrace-research |
| 站点类型 | 静态展示页（无后端/无数据库/无API） |
| 源文件目录 | `showcase/` (index.html + assets/) |
| 目标仓库分支 | `competition/26-qoder-research-desk` |
| 仓库地址 | https://github.com/fanbaodong/powercontext-2026 |

## 发布状态

| 字段 | 值 |
|------|-----|
| 已发布 | **true** |
| 公开状态 | **public**（任何人可访问） |
| readiness | publication_completed |
| lifecycle | active |
| governance_status | normal |

## 匿名访问实测

2026-10-30 23:47（Asia/Shanghai）在全新无登录浏览器上下文访问最终公开网址：HTTP 200，无登录重定向，4 张截图全部加载，390px 宽页面无横向溢出。检查前 Cookie 数为 0；使用独立测试实例，没有复用参赛者的登录浏览器。结构化结果见 [site-anonymous.json](evidence/site-anonymous.json)。

## 官方标识符

| 标识 | 值 |
|------|-----|
| actionId | `eda369b1-2a58-4ec3-ab38-92a0d5b30b55` |
| projectId | `01a18d6c-957b-7918-95c1-ea14b4a6139f` |
| siteId | `01a18d6c-957e-7653-a7c3-1f1dbfea91e7` |
| verificationOperationId | `01a18d6c-98fd-71a1-9445-3fd57b5bebf3` |
| publishOperationId | `01a18d71-3ffb-7ca1-8a6e-167d8d6a10a0` |
| Qoder 账号 UID（参赛者提供） | `1918138641918910` |

账号 UID 由参赛者本人提供。官方 CLI 的 `get_site` 返回对象未单独提供 owner/creator/account UID，未通过头像路径、用户名或站点 ID 推测账号 UID。

## 发布流程实际执行记录

1. `get_context` — 确认 projectRoot，无已有站点
2. `prepare_site --input-file .sites-work/prepare.json` — 创建草稿，返回 actionId/projectId/siteId
3. `get_publish_status` — 等待 artifactReady=true, canPublish=true
4. `publish_site --input '{"actionId":"..."}'` — 提交发布，返回 publishOperationId
5. `get_publish_status` — 确认 published=true, readiness=publication_completed
6. `get_site_access` — 读取 accessRef，确认 public 可用
7. `update_access_policy --input-file .sites-work/public.json` — 设置 mode=public, confirmPublic=true
8. `get_site --input-file .sites-work/get-site.json` — 确认 access_mode=public，读取项目元数据

## showcase/index.html 内容摘要

1. **痛点展示**：科研阅读三个痛点（找不到/链不上/追不动）
2. **真实截图**：4 张主机真实浏览器截图（library/search/cards/mobile.png）
3. **seekdb 核心作用**：FULLTEXT ngram、NATURAL LANGUAGE MODE、幂等导入、事务原子性，含核心 SQL
4. **验证结果**：pytest 45/45、集成 41/41、API 27/27、浏览器 8/8、重启持久化通过
5. **本地声明**：明确标注为本地单人应用，本展示页为静态评审入口
6. **无伪造数据声明**：不编造用户规模/提效百分比/论文引用
7. **仓库链接**：指向 `fanbaodong/powercontext-2026` 分支 `competition/26-qoder-research-desk/qoder`
8. **响应式布局**：截图网格移动端单列
