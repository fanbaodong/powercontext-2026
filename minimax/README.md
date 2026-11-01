# MiniMax SDD：可追溯的工单筛选修改

工程上下文来自 **seekdb 的实际 SQL 查询**；任务进展通过 **PowerContext 1.2.0 Memory SDK** 保存；代码修改通过 **MiniMax 官方 API** 生成。开发基于官方 `2026-competition-ecosystem`，基线 `d647275c308e09b7b623f322a26f5c4e85734fb0`。

## 当前状态

2026-11-01 已完成真实开发验证。MiniMax-M3.1-Flash 根据实际检索上下文生成了标签筛选修改，7 个目标测试从 7 个业务断言失败变为全部通过，12 个回归测试前后全部通过。PowerContext 在新进程恢复完成状态并跳过重复修改。模块的 45 项测试通过。

`sample/` 保留可复现的修改前样例；**最终业务实现位于 `evidence/after/tickets.py`**，是模型原始最终代码，未由另一模型或人工代写。运行 `python evidence/after/tickets.py --status open --tag security --limit 1` 返回工单 ID 9。

| 验收项 | 实际记录 |
| --- | --- |
| 真实 MiniMax 修改 | `evidence/minimax-record.json`：请求任务、上下文、模型输出、响应 ID 和 token 使用量 |
| 最新上下文 | `evidence/context-v1.json`、`context-v2.json`：同路径 ANY → ALL，revision 1 → 2 |
| 数据库实际参与 | `evidence/database.json`、`import-*.json`、`context-*.json`：真实 seekdb 身份、SQL 和结果 |
| 状态重启复用 | `evidence/state-saved-*.json`、`state-restored-*.json`：SDK 引用、不同进程、完整状态 |
| 同一测试前后验证 | `evidence/before-tests.json`、`after-tests.json`、对应 `.log`，测试哈希一致 |
| 完整代码和可运行说明 | 下方安装、CLI、重放步骤；`evidence/before/`、`after/` 和 `change.diff` |

历史准备记录保留在 `preflight-evidence/`；最终结果以 `evidence/` 为准。

## 环境与安装

- Python 3.14（开发环境 3.14.3）；依赖精确版本见 `requirements.lock`。
- Docker Engine / Compose v2。
- seekdb 镜像固定为 `oceanbase/seekdb:1.4.0-100000172026082615`。
- 默认 MiniMax CN `https://api.minimax.cn/v1`、`MiniMax-M3.1-Flash`；海外账号可改为 `https://api.minimax.io/v1`。以账号实际可用模型为准。

在本文件所在 `minimax/` 目录运行（Linux/macOS）：

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
docker compose up -d --wait seekdb
python -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.example .env
docker compose up -d --wait seekdb
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

如果使用已有 seekdb/OceanBase，将 `.env` 中 `SEEKDB_*` 改为实际连接；仅在名称为 `minimax_...` 的专用数据库内创建 `sdd_documents`，不清空其他库。Compose 默认端口 `127.0.0.1:2893`，独立卷 `minimax-sdd-data`。修改初始数据库密码后，已有卷不会自动重置密码。

MiniMax Key 只放入忽略提交的 `.env`。调用前才读取，不写日志、不传给样例测试进程。SDK 的模型相关可选依赖不会另行调用模型；PowerContext 只使用 SQLite Memory 持久化能力。

## 任务和固定验收样例

`sample/tickets.py::select_tickets` 已有状态过滤、ID 排序和 limit，尚未实现标签过滤。任务 `fixtures/task.json` 要求按最新规格增加标签过滤：两侧 `strip().casefold()`、空白/重复忽略、请求中的**所有**标签均满足。不得改变原有行为或输入数据。

必要来源：

| 文件 | 用途 |
| --- | --- |
| `sample/specs/ticket_filters.md` | 有效规格来源，含 REQ-TAG-001 和兼容约束 |
| `sample/tickets.py` | 修改位置 `select_tickets` |
| `sample/tests/test_tags.py` | 固定 7 个目标测试 |
| `sample/tests/test_regression.py` | 固定 12 个回归测试 |
| `sample/tickets.json` | 初始工单数据 |

初始规格 v1 为 ANY；`prepare` 导入 v1，查询后用 `fixtures/ticket_filters_v2.md` 覆盖同源，再次导入/查询，验证内容和版本都已更新为 ALL。`sample/` 始终保留原始可复现实例，所有运行修改在新建的 `--run` 目录中。支付收据文档是无关资料，必须不进入本任务上下文。

## CLI 输入输出与真实开发流程

以下的 `python` 指激活后的虚拟环境；Windows 也可替换为 `.\.venv\Scripts\python.exe`。

```bash
python -m sdd_context prepare --run .runs/development
python -m sdd_context resume --run .runs/development
python -m sdd_context resume --run .runs/development --generate
# 阅读 .runs/development/candidate.py 和 minimax-record.json 中的最终修改。
python -m sdd_context resume --run .runs/development --apply
python -m sdd_context resume --run .runs/development
python -m sdd_context export --run .runs/development --output evidence
```

- `prepare`：创建独立 project ID；实际导入/检索 v1 和 v2；保存源码快照，目标测试须因行为未实现失败，回归须通过；向空 PowerContext 状态写入 checkpoint。
- `resume`：在**新的 Python 进程**打开 SDK，精确读取 task ID 的最新 revision；实际使用完成项、待办和测试结果，选择后续动作。若全部验证完成，跳过模型修改。
- `--generate`：把真实检索内容、来源/哈希、恢复状态和初始代码发给 MiniMax；只保存候选，不执行。一次调用，无自动重试。
- `--apply`：只允许替换 `tickets.py`，校验任务、上下文、基线绑定；执行相同的 7+12 测试并检查确实执行、无测试改动；验证 CLI 返回 ID `[9]`，写入新状态。测试失败会保留日志并恢复原始源码。
- `export`：只导出 JSON/日志/diff/前后快照，不导出 `.env`、SDK 数据库或认证信息。

输出是 JSON；运行目录记录 SQL、绑定参数、数据库身份、文档 revision/哈希/内容、来源行号和 AST 符号范围。状态写入引用包含实际 SDK 版本、artifact/revision/entry ID，以及写入/恢复进程 PID。

## 干净环境重放

评审核对开发记录即可，无须模型再次生成相同代码，也无须 Key：

```bash
python -m sdd_context prepare --run .runs/review
python -m sdd_context resume --run .runs/review
python -m sdd_context resume --run .runs/review --replay-record evidence/minimax-record.json
python -m sdd_context resume --run .runs/review
```

首次安装完成后，以上复现不需要配置 MiniMax Key。也可运行 `python verify_evidence.py` 核对交付记录、前后源码、固定测试与模型输出的一致性。

重放明确记录为 `replay-saved-model-change`，不会伪装成新模型调用。新的数据库项目 ID 和空状态独立建立；调用记录须与任务、源码和当前规格内容对应。

重置采用新的 `--run` 目录即可，旧证据不覆盖。关闭本题服务用 `docker compose stop seekdb`。模块为单机、单写者示例；SQL 采用参数化词项匹配，最多选 24 个相关文档，不主张替代大型工程的混合检索。模型候选是普通 Python，执行前应阅读；目录白名单和环境过滤不是操作系统沙箱。

## 技术与来源

- [一页技术说明](TECHNICAL.md)
- [PowerContext 官方 Memory API](https://github.com/oceanbase/powercontext/blob/powercontext-v1.2.0/docs/zh/development/memory-layer.md)
- [seekdb 官方镜像说明](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)
- [MiniMax OpenAI 协议](https://platform.minimax.cn/docs/api-reference/text-chat-openai)

业务数据、规格、测试和上下文适配模块为本题新建；PowerContext 使用官方 PyPI SDK，不拷贝或替代其持久化实现。
