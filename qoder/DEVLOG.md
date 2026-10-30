# 研读台 ReadTrace - 开发日志

## 项目信息

- **项目名称**：研读台 ReadTrace
- **开发时间**：2026-10-30
- **开发者**：Qoder CLI（AI 辅助开发）
- **工作区**：`powercontext/qoder`
- **赛事**：2026 OceanBase 数据库大赛

## 开发过程

### 阶段 1：需求分析与架构设计

- 分析需求文档，明确功能边界
- 设计数据库 Schema（5 张表 + FULLTEXT 索引）
- 确定技术栈：Python/FastAPI + seekdb + 原生前端
- 编写 ARCHITECTURE.md 架构文档

### 阶段 2：后端实现

**完成内容**：
- `app/config.py`：配置管理（环境变量 + 默认值）
- `app/database.py`：数据库连接管理（PyMySQL + context manager，每次请求新建连接）
- `app/models.py`：Pydantic 数据模型（输入验证）
- `app/services/chunking.py`：段落切块逻辑
- `app/services/project.py`：项目管理
- `app/services/document.py`：文档导入（幂等 + 切块）
- `app/services/search.py`：全文检索（MATCH...AGAINST）
- `app/services/card.py`：证据卡片（收藏/导出）
- `app/services/progress.py`：阅读进度
- `app/routers/*.py`：RESTful API 路由
- `app/main.py`：FastAPI 应用入口

**关键设计决策**：
1. 使用 SHA-256 哈希实现幂等导入
2. FULLTEXT 索引使用 ngram 分词器支持中文
3. 所有 SQL 参数化，防止注入
4. 项目隔离通过 `project_id` 外键强制

### 阶段 3：前端实现

**完成内容**：
- `app/static/index.html`：双栏布局 + 弹窗
- `app/static/css/style.css`：深蓝/纸白/青绿配色
- `app/static/js/app.js`：单页应用逻辑

**关键特性**：
1. 所有用户内容使用 `escapeHtml()` 渲染，防止 XSS
2. 来源链接仅允许 http/https，添加 `rel="noopener"`
3. 响应式设计，支持桌面和移动端
4. 明确的空状态、加载中、错误提示

### 阶段 4：测试与验证

**完成内容**：
- `tests/test_chunking.py`：切块逻辑单元测试（11 个用例）
- `tests/test_validation.py`：输入验证单元测试（15 个用例）
- `tests/test_urls.py`：URL 安全单元测试（11 个用例）
- `scripts/integration_test.py`：seekdb 真实集成测试（30+ 个检查点）

**测试覆盖**：
- 切块：段落分割、长段落处理、空白过滤、编号连续性
- 验证：空内容、超长字段、非法扩展名、危险 URL
- 集成：导入/重复/项目隔离/搜索/卡片/进度/持久化/非法输入

### 阶段 5：文档与演示

**完成内容**：
- `README.md`：使用说明、快速启动、API 文档
- `ARCHITECTURE.md`：系统架构、数据模型、安全设计
- `scripts/load_demo_data.py`：8 篇原创演示笔记
- `scripts/init_db.sql`：数据库初始化脚本
- `showcase/index.html`：项目展示页（占位）
- `.env.example`：配置示例
- `docker-compose.yml`：容器化部署
- `Dockerfile`：应用镜像
- `requirements.txt`：依赖清单
- `.gitignore`：忽略规则

## 主机真实验证结果

2026-10-30 主机运行 seekdb 真实集成测试（27 项 API 检查）：

- **26 项通过**：项目创建/隔离、文档导入/幂等、跨项目独立、全文检索（NATURAL LANGUAGE MODE）、证据卡片 CRUD、阅读进度、持久化、非法输入处理
- **1 项失败**：同一批注重复 PUT 返回 404（原因：MySQL UPDATE 在数据未变时 rowcount=0，被误判为"不存在"）
- **已修复**：`update_card_annotation` 改为先 SELECT 检查存在性，再 UPDATE

关键发现：
1. BOOLEAN MODE 搜索 "间隔重复" 返回 0 结果 → 改用 NATURAL LANGUAGE MODE 后命中 4 行
2. `AS read` 是 MySQL 保留字 → 改用 `AS read_count` 别名
3. seekdb collation 仅支持 `utf8mb4_general_ci`（不支持 `utf8mb4_unicode_ci`）
4. 短段落过滤（chunk_min_chars=20）会丢失标题和关键点 → 移除过滤

## 已知限制

1. **搜索类型**：仅支持关键字匹配（FULLTEXT），非语义/向量检索
2. **文件类型**：仅支持 .txt 和 .md
3. **文件大小**：最大 10MB
4. **URL 协议**：仅允许 http/https
5. **部署模式**：单人本地，无用户认证
6. **网络绑定**：仅 loopback (127.0.0.1)

## 待主机验证后修复

如果主机运行验证发现错误，请提供：
- 错误日志（终端输出）
- 浏览器控制台错误（F12）
- 数据库错误信息
- 复现步骤

Qoder 将根据真实错误信息进行修复。

## 数据来源声明

- **演示数据**：8 篇原创中文学习方法笔记，全部明确标为【演示笔记】
- **不伪造**：无真实论文引用、无虚假用户数据、无编造提效数字
- **用途**：仅供功能验证和界面展示

## 开发工具

- **AI 助手**：Qoder CLI
- **编程语言**：Python 3.10+
- **框架**：FastAPI
- **数据库**：seekdb (OceanBase MySQL 兼容)
- **前端**：原生 HTML/CSS/JS
- **测试**：pytest

## 时间线

- 2026-10-30：完成全部代码编写（首轮，未运行）
- 2026-10-30：第一轮修复（seekdb collation、切块逻辑、原子事务、XSS 防护等）
- 2026-10-30：第二轮修复（NATURAL LANGUAGE MODE、read 保留字、前端状态管理等）
- 2026-10-30：主机运行真实 seekdb 集成测试，27 项 API 检查中 26 项通过
  - 唯一失败：同一批注重复 PUT 返回 404（update_card_annotation 使用 UPDATE rowcount==0 判断不存在，MySQL 未变数据 rowcount 为 0）
  - 修复：改为先 SELECT 检查存在性，再 UPDATE
- 2026-10-30：第三轮修复
  - init_db.py 添加 `[A-Za-z_][A-Za-z0-9_]*` 严格校验防止 SQL 注入
  - Markdown 导出改用 `_escape_md_text` 转义（保留原始字符，不删除用户内容）
  - 测试用例修正（短段落不再过滤、长段落 fixture 超过 2000 字符）
  - 集成测试重构为 try/finally 清理、显式 ValueError 捕获
  - 演示笔记去掉无来源效果统计（"高出约20%"、"平均需要23分钟"）
  - load_demo_data 跟踪单独失败，有任何失败则返回 False
  - 文档更新端口 2896/8036、init_db.py 命令、NATURAL LANGUAGE MODE 描述
- 待主机验证：运行最终验证、补充真实截图

---

**本日志由 Qoder CLI 据实记录，未编造运行成功或作者归属。**

## 主机最终验收补充（Codex记录）

45项pytest、41项真实数据库集成、27项独立API、8项浏览器检查均通过；实际停止并重启uvicorn后文档、批注与进度保留。Codex追加了请求序号缺省值、手机布局、JSON字节限制、旧测试引用、长段落边界空白和演示文字审校等小修复；未将这些修复归于Qoder。所有验收步骤、分工与原始会话导出见ACCEPTANCE.md和PROVENANCE.md。Sites发布结果单独记录，未冒充已经取得大赛得分。

## 2026-10-30 23:47：Sites公开发布与最终交付

真实Qoder会话通过官方Sites命令创建、上传并发布站点，将访问范围设为public。Codex使用全新无登录浏览器确认HTTP 200、4张实际截图正常加载及移动布局；公开网址、官方站点标识、参赛者提供的UID与匿名访问证据见SITES_DELIVERY.md。未向赛事平台提交评分请求，未声称已取得分数。
