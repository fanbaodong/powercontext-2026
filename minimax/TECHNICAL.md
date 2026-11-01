# MiniMax SDD：工单筛选上下文模块

2026-11-01 已用真实 MiniMax-M3.1-Flash 完成一次修改：7 个目标测试由业务断言失败变为全部通过，12 个回归测试前后通过。命令、退出码及技术使用事实在 `evidence/`，平台最终评分以实际评测为准。

## 任务与模块边界

业务样例是可独立运行的 Python 工单筛选器。初始 `sample/tickets.py::select_tickets` 接收但忽略标签参数；任务是在保持状态筛选、排序、分页及输入数据不变的前提下，实现当前规格的标签过滤。`fixtures/task.json` 指明唯一可编辑文件、必要来源和固定测试命令。

| 组件 | 在本次任务中的作用 |
| --- | --- |
| seekdb / OceanBase | 持久化规格、源码和测试资料；实际 SQL 查询结果组成开发上下文。连接使用 PyMySQL 1.1.2。 |
| 上下文模块 | 按任务选择相关资料，返回规格条目、代码文件或符号、目标与回归测试，以及恢复的任务状态。 |
| MiniMax | 接收检索上下文和初始源码，生成 `tickets.py` 的实际修改。使用官方 HTTPS Chat Completions API，模型及使用量记入调用证据。 |
| PowerContext | 使用 `powercontext[builtin]==1.2.0` 的真实 Memory API 保存进展与测试结果，SQLite 持久化由 SDK 管理。 |
| 验证流程 | 保存前后源码、统一差异和原始测试输出，核对代码版本与测试文件哈希。 |

## 资料来源与规格更新

导入范围限定为样例工程，来源使用相对路径标识；上下文须保留真实来源、行范围及内容 SHA-256，便于对照原文件。数据库查询使用参数绑定，任务选择依据来源约束与检索结果，不将模型输出作为 SQL。无关的付款收据规格用于检查资料选择是否将全部文件无差别返回。

`specs/ticket_filters.md` 首次导入为 revision 1（ANY）。随后将同一逻辑来源更新为 `fixtures/ticket_filters_v2.md` 中的 revision 2（ALL），执行 upsert 并重新查询；最终上下文必须体现新规则及新哈希，不混入旧 ANY 规则。标签按 `strip().casefold()` 归一化，忽略空白与重复请求标签；现有状态筛选、排序和 limit 语义保留。

## 任务状态与真实模型修改

`sdd_context/state.py` 通过 `runtime.memory.for_scope(scope).remember()` 写入 JSON 状态，包含 `task_id`、`completed`、`pending`、`test_results` 和 `code_version`。后续独立进程重新打开同一数据库，调用 `list()`，按精确 task_id 筛选并选取最新 `created_in_revision`。读取结果必须进入后续上下文或决定待执行步骤，而非仅展示“连接成功”。证据保留 Memory revision、entry ID 和 SDK 版本，不记录连接凭据。

`sdd_context/minimax_client.py` 只接受最终 JSON 的 `path/content/summary` 三个字段。应用阶段仅允许任务指定的 `tickets.py`，禁止修改测试、规格或其他文件；模型客户端本身不执行生成代码。调用记录保存任务、实际提供的上下文、最终代码及必要响应元数据，不保存私有思维链。

## 复现与证据标准

从干净副本执行相同目标测试：修改前应因标签行为缺失发生断言失败，修改后应全部通过；原有回归测试前后均须通过。两次运行保持同一测试文件及断言，并记录完整命令、工作目录、退出码、代码版本及文件哈希。源码前后快照、diff、数据库导入/查询、规格更新、PowerContext 跨进程恢复和真实 MiniMax 调用构成完整验证链。

密钥由本地环境配置提供，`.env`、运行数据库和其他凭据不进入仓库。没有真实模型调用或某一步验证失败时，应如实记录未完成项，不用预写答案、模拟响应或环境错误替代必做项证据。
