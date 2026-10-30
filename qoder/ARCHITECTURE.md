# 研读台 ReadTrace - 架构设计

## 系统概览

```
┌─────────────────────────────────────────────────────────┐
│                    浏览器 (前端)                          │
│  HTML/CSS/JS 单页应用 · 双栏布局 · 中文界面               │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/JSON
                       ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI 应用 (后端)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Routers  │→ │ Services │→ │ Database │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  端口 8036 · 仅绑定 127.0.0.1                            │
└──────────────────────┬──────────────────────────────────┘
                       │ PyMySQL
                       ▼
┌─────────────────────────────────────────────────────────┐
│              seekdb (OceanBase)                          │
│  qoder_research 库                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ projects │  │documents │  │  chunks  │              │
│  └──────────┘  └──────────┘  │FULLTEXT  │              │
│  ┌──────────┐  ┌──────────┐  └──────────┘              │
│  │  cards   │  │ progress │                            │
│  └──────────┘  └──────────┘                             │
│  端口 2896 · 仅绑定 127.0.0.1                            │
└─────────────────────────────────────────────────────────┘
```

## 数据模型

### projects (研究项目)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增主键 |
| name | VARCHAR(255) | 项目名称 |
| description | TEXT | 项目描述 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### documents (资料文档)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增主键 |
| project_id | INT FK | 所属项目 |
| title | VARCHAR(500) | 标题 |
| author | VARCHAR(255) | 作者（可选） |
| source_url | VARCHAR(2000) | 来源链接（可选） |
| tags | VARCHAR(1000) | 标签（逗号分隔） |
| content_hash | CHAR(64) | 全文 SHA-256 |
| char_count | INT | 字符数 |
| chunk_count | INT | 分块数 |

**唯一约束**: `(project_id, content_hash)` — 同项目相同内容幂等导入

### chunks (段落分块)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增主键 |
| document_id | INT FK | 所属文档 |
| paragraph_number | INT | 段落序号（从1开始） |
| content | TEXT | 段落内容 |
| content_hash | CHAR(64) | 段落 SHA-256 |
| char_count | INT | 字符数 |

**索引**: `FULLTEXT INDEX ft_content (content) WITH PARSER ngram`

### cards (证据卡片)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增主键 |
| project_id | INT FK | 所属项目 |
| document_id | INT FK | 来源文档 |
| chunk_id | INT FK | 来源段落 |
| annotation | TEXT | 批注内容 |

**唯一约束**: `(project_id, document_id, chunk_id)` — 同项目同段落不重复收藏

### reading_progress (阅读进度)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增主键 |
| document_id | INT FK UNIQUE | 所属文档 |
| status | ENUM | unread/reading/read |

## 核心流程

### 1. 资料导入流程

```
用户输入/上传
    ↓
验证（扩展名、大小、空内容、URL安全）
    ↓
计算全文 SHA-256
    ↓
检查 (project_id, content_hash) 唯一约束
    ├─ 已存在 → 返回现有文档（幂等）
    └─ 不存在 → 继续
         ↓
    按段落切块（双换行分割，长段落按句子再分）
         ↓
    计算每个块的 SHA-256
         ↓
    INSERT documents + chunks + reading_progress
         ↓
    返回文档信息
```

### 2. 全文检索流程

```
用户输入关键字
    ↓
验证非空
    ↓
清理特殊字符（保留中文、字母、数字、空格）
    ↓
构建 NATURAL LANGUAGE MODE 查询
    ↓
执行 SQL:
  SELECT ... MATCH(c.content) AGAINST(? IN NATURAL LANGUAGE MODE) AS relevance
  FROM chunks c JOIN documents d ON ...
  WHERE d.project_id = ?
    AND MATCH(c.content) AGAINST(? IN NATURAL LANGUAGE MODE)
  ORDER BY relevance DESC
    ↓
返回结果（包含 document_id, chunk_id, 段落号, 相关性得分）
```

### 3. 证据卡片流程

```
用户点击"收藏为卡片"
    ↓
弹出批注输入框（可选）
    ↓
INSERT cards (project_id, document_id, chunk_id, annotation)
ON DUPLICATE KEY UPDATE annotation = VALUES(annotation)
    ↓
卡片保存成功，包含完整出处信息
    ↓
导出时生成 Markdown：
  - 来源文档标题
  - 段落编号
  - document_id / chunk_id
  - 原文摘录
  - 批注内容
```

## 安全设计

### XSS 防护

- 前端所有用户内容使用 `textContent` 渲染（通过 `escapeHtml()` 函数）
- 不使用 `innerHTML` 插入未转义内容
- 来源链接仅允许 http/https 协议，添加 `rel="noopener noreferrer"`

### SQL 注入防护

- 所有 SQL 使用参数化查询 (`%s` 占位符)
- FULLTEXT 查询关键字经过白名单过滤（仅保留中文、字母、数字、空格）
- 表名和列名使用固定标识符，不接受用户输入

### URL 安全

- `validate_safe_url()` 函数检查协议头
- 仅允许 `http://` 和 `https://` 开头
- 拒绝 `javascript:`, `data:`, `file:` 等危险协议

### 输入验证

- Pydantic 模型强制类型和长度约束
- 文件大小限制 10MB
- 仅允许 .txt 和 .md 扩展名
- 空内容拒绝

## 切块策略

1. **统一换行符**：将 CRLF/CR 统一转为 LF
2. **按段落分割**：使用正则 `\n{2,}` 分割
3. **过滤空白**：去除空段落和纯空白段落
4. **保留所有非空段落**：包括短标题和关键点（不再按最小字符数过滤）
5. **长段落处理**：超过 2000 字符的段落按句子边界再分割，不丢失字符
6. **重新编号**：段落号从 1 开始连续编号

## 持久化保证

- 所有数据存储在 seekdb 中
- 浏览器刷新：前端从 API 重新加载数据
- 后端重启：数据库连接重建（每次请求新建连接，autocommit=False），数据不受影响
- 阅读进度：独立的 `reading_progress` 表，UPDATE 操作原子性

## 项目隔离

- 每个文档通过 `project_id` 关联到项目
- 搜索查询强制 `WHERE d.project_id = ?`
- 卡片通过 `project_id` 筛选
- 统计按项目聚合
- 删除项目级联删除所有相关文档、分块、卡片

## 部署选项

### 选项 A：本地直接运行

```bash
# 终端 1：启动 seekdb
# （使用 Docker Compose: docker-compose up -d）

# 终端 2：初始化数据库
python scripts/init_db.py

# 终端 3：启动应用
python -m uvicorn app.main:app --host 127.0.0.1 --port 8036
```

### 选项 B：Docker Compose（仅数据库）

```bash
docker-compose up -d
# 启动 seekdb 数据库容器（端口 2896）
# 应用需从本机 venv 单独启动
python scripts/init_db.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8036
```

## 性能考虑

- FULLTEXT 索引使用 ngram 分词器，支持中文检索
- 搜索结果限制默认 50 条，最大 200 条
- 数据库连接：每次请求新建连接，autocommit=False，显式 commit/rollback
- 前端静态文件由 FastAPI 直接服务（生产环境建议使用 Nginx）
