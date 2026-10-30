# 研读台 · ReadTrace

把散落的学习笔记整理成能检索、能定位出处、能继续阅读的资料库。流程：导入 TXT/Markdown → seekdb 全文搜索 → 收藏原段落并批注 → 保存阅读状态 → 导出带出处的 Markdown。

这是本地单人应用。Qoder Sites 提供公开的作品介绍、真实截图和复现入口，数据库与资料仍保存在本机。仅实现关键词全文检索，不宣称语义/向量搜索。

公开项目网站：[研读台 ReadTrace](https://readtrace-research-edswj48b142.qoder.zone)。

- 网站与发布信息：[SITES_DELIVERY.md](SITES_DELIVERY.md)
- 实际验收：[ACCEPTANCE.md](ACCEPTANCE.md)
- 操作样例：[VERIFICATION.md](VERIFICATION.md)
- Qoder 开发归属及记录：[PROVENANCE.md](PROVENANCE.md)

## 技术与版本

主要功能由官方 Qoder CN CLI 1.1.81 在真实登录会话中开发。FastAPI 提供 API 与原生 HTML/CSS/JS；PyMySQL 连接真正的 seekdb。资料、段落、阅读进度和证据卡片均写入数据库；检索使用 `FULLTEXT ... WITH PARSER ngram` 与参数化 `MATCH(content) AGAINST(... IN NATURAL LANGUAGE MODE)`。

验收环境：Python **3.12**、seekdb **1.4.0**、Windows 11 / Ubuntu WSL、Edge 无登录测试实例。建议 Python 3.12；依赖版本见 [requirements.txt](requirements.txt)。无需云模型 Key、Node 或 MySQL 命令行客户端。

## 安装与启动

先安装 Python 3.12 与 Docker（Windows 可使用 Docker Desktop 的 Linux 容器）。先克隆仓库，再进入项目的 `qoder` 目录执行后续安装与启动命令。

```bash
git clone --branch competition/26-qoder-research-desk https://github.com/fanbaodong/powercontext-2026.git
cd powercontext-2026/qoder
```

Linux/macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell（已安装 Python 3.12）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，把 `DB_PASSWORD=placeholder_password` 改为自己的本地数据库密码。默认数据库端口 `2896`，应用端口 `8036`；不要提交 `.env`。

```bash
docker compose up -d seekdb
```

首次启动需等待数据库可连接，可用 `docker compose logs --tail=40 seekdb` 查看启动日志。下面的初始化脚本可重复运行，连接拒绝时等待启动完成再重试；不需要删除数据卷。

Linux/macOS（已激活 venv）：

```bash
python scripts/init_db.py
python scripts/load_demo_data.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8036
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe scripts/init_db.py
.\.venv\Scripts\python.exe scripts/load_demo_data.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8036
```

打开 **http://127.0.0.1:8036**。API 文档：`/docs`，数据库连接状态：`/api/health`。若复用已有 seekdb，在 `.env` 填写其连接参数后跳过 `docker compose up`；应用仍使用独立 `qoder_research` 库。验收时复用了本机端口 2891 的同版本 seekdb，不是默认示例端口。

Compose 只部署固定镜像 `oceanbase/seekdb:1.4.0-100000172026082615` 并将端口绑定 loopback；它不会启动 Web 应用。卷 `seekdb_data` 保存数据。`docker compose stop` 可停止数据库，恢复用 `docker compose start`。

## 可体验的流程

1. 选择“认知科学笔记”，在检索页输入“间隔重复”。结果包含真实全文索引分数、资料名和段落号。
2. 点击“查看原文”，把阅读状态设为“在读”；刷新后重新打开，状态仍保留。
3. 收藏一段、填写批注，在“证据卡片”修改批注并导出 Markdown。
4. 在新项目粘贴自己的笔记或上传 UTF-8 `.txt` / `.md`；同项目相同内容重复导入返回同一文档，不重复保存。不同项目可以独立保存同一资料。

所有演示资料均标为“演示笔记”；没有声称已获得真实用户、访问量或节省时间的统计。自然语言全文模式按分词相关性匹配，结果可能命中查询的部分词，不等同于整句精确匹配。

## 验证

激活虚拟环境后运行（PowerShell 可把 `python` 换为 `.\.venv\Scripts\python.exe`）：

```bash
python -m pytest tests/ -q
python scripts/integration_test.py
python scripts/host_acceptance.py evidence/api-acceptance-local.json
```

后两条需要真实 seekdb，创建独立测试项目，结束后清理其自身项目；数据库不可用和断言失败均返回非零状态。集成脚本的“持久化”指重新连接查询；实际终止并重启后端的额外验收见 [evidence/restart-verification.json](evidence/restart-verification.json)。

## 边界

- 本地单人模式，没有多用户登录、授权或互联网部署配置；不要把 API 绑定公网。
- 文件与粘贴文本最多 10 MiB，UTF-8，仅 `.txt` / `.md`；不自动抓取来源 URL，不解析 PDF。
- 大文件请求尚无流式解析，适合个人学习笔记规模。`source_url` 仅允许 HTTP(S)。
- 段落按空行及长度拆分；首尾空白及换行形式会规范化，段落号是导入后的块序号，不是 PDF 页码。
- 开源依赖沿用各自许可；演示资料由 Qoder 生成并经主机审校，不冒充论文或实验数据。
