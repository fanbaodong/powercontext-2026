# 记仇杂货铺：老葛的奇怪账本

你走进一家卖“会打嗝的月亮”的杂货铺。恶作剧能挣到零花钱，却让老板涨价；帮忙和道歉可以修复关系。店主会翻出你的旧账，集齐三件怪东西即可获得“终身奇怪顾客”称号。

这是一个 **Dify Workflow + seekdb + 本地 Ollama** 应用。模型负责有趣的对白，数据库中的规则负责金币、关系和物品。即使玩家要求模型“凭空送一万金币”，账本也不会因此改写。

## 交付文件与分工

| 文件/组件 | 作用 |
| --- | --- |
| `grudge-shop.yml` | 导入 Dify 的唯一 DSL：输入 → 编码 → HTTP 读写 seekdb 服务 → 核验上下文 → HTTP 本地模型 → 渲染 → 输出 |
| `backend/app.py` | 事务内更新玩家状态、读回最近四次事件；请求幂等、并发保护、参数校验 |
| `workflow_code/` | DSL 中 Code 节点的可审查源码，用 `scripts/build_dsl.py` 生成 DSL |
| `scripts/prepare_deploy.py` | 下载固定 Dify 官方 Docker 配置，校验文件 hash，生成本地随机密码和 Compose 补充配置 |
| `scripts/start.py` | 启动本地应用并下载模型；`--stop` 仅停止，不删除存档 |
| `scripts/verify_backend.py` | 真实 seekdb 后端集成验证；不是 Dify 引擎模拟评分 |
| `scripts/verify_dify.py` | 通过本机 Dify 控制台 API 导入、运行六组样例、核对数据库状态并发布；只用于本地验证 |
| `evidence/` | 实际验证记录，明确记录验证范围 |

Dify 是核心编排入口；seekdb 保存 `dify_grudge_shop.players` 和 `events`，历史内容进入模型提示词，也直接展示在“店主翻出的旧账”中。Dify 自身的账户和工作流元数据仍使用官方部署中的 PostgreSQL，**不将其宣称为 seekdb**。本应用不使用 PowerContext、Embedding、Rerank 或额外插件。

## 环境

- Linux x86_64 或 Windows WSL2，Docker Engine 与 Compose v2.24+，Python 3.10+。
- 建议分配 4 核、至少 10 GB 内存、20 GB 空闲磁盘；下载镜像和模型需要访问 GitHub、Docker Hub、Ollama 模型仓库。小内存设备可减少并发，但不能保证同时运行其他数据库编译任务。
- 固定 Dify **1.9.1**，源码 commit `cd47a47c3b4acc4d4457b73777722602c33bc950`，DSL `0.4.0`。
- seekdb 镜像 `oceanbase/seekdb:1.4.0-100000172026082615`；Ollama 镜像 `ollama/ollama:0.11.10`；模型 `qwen3:0.6b`，CPU 可运行，无需云模型密钥。
- 后端 Python 3.12；依赖固定在 `backend/requirements.txt`。模型生成耗时受 CPU 影响，单次 HTTP 最长等待 120 秒。

## 从零部署

以下在本仓库的 `dify/` 目录执行。Windows 推荐把目录复制到 WSL 的 Linux 文件系统中再部署，避免 PostgreSQL bind mount 权限问题。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/start.py
```

Windows 原生 Python 的激活命令为 `.venv\Scripts\Activate.ps1`；Docker Desktop 必须先正常启动。本机如 Docker Desktop 故障，可使用 Ubuntu WSL 内独立 Docker Engine，不需要删除 Desktop 数据。

脚本只创建项目 `grudge-shop` 的服务、卷和 `.runtime/` 文件。上游配置固定到指定 commit；数据库首次启动可能需要数分钟，`shop` 会在数据库未就绪时重启，直到初始化成功。初始化只新建专属库和表，不需要外部素材。

```bash
cd .runtime/docker
docker compose -p grudge-shop -f docker-compose.yaml -f compose.shop.yaml ps
docker compose -p grudge-shop -f docker-compose.yaml -f compose.shop.yaml logs --tail=50 shop
curl http://127.0.0.1:8099/health
```

健康接口成功表示后端可查询数据库版本；这一步不替代完整玩法验证。

### 导入与体验

1. 打开 `http://127.0.0.1:8088/install`，设置仅供本机使用的 Dify 管理员账户。
2. Studio → 导入 DSL 文件，选择 `grudge-shop.yml`。无需安装模型供应商插件或绑定知识库。
3. 在工作流环境变量面板，按 `.runtime/import-settings.local.json` 填写以下值。不要把该私密文件提交 Git 或截图公开。

   | 变量 | 类型 | 值 |
   | --- | --- | --- |
   | `SHOP_BASE_URL` | String | `http://shop:8099` |
   | `SHOP_API_TOKEN` | Secret | 本地随机生成值，必须与后端相同 |
   | `OLLAMA_BASE_URL` | String | `http://ollama:11434` |
   | `OLLAMA_MODEL` | String | `qwen3:0.6b` |

4. 点击 Test Run，输入 `player_id`（3–64 位字母/数字/下划线/短横线）、动作和可选台词。`player_id` 是存档代号，不是姓名。`visit` 看看，`prank` 恶作剧，`help` 帮忙，`apologize` 道歉，`buy` 购买。
5. 发布工作流并打开 Dify 生成的 Web App；之后也可从该入口体验。每次选择一个动作，保持相同代号会继续存档，换新代号开始新局。

后台 HTTP 服务通过 Compose 内部网络相连。SSRF 代理只额外允许 `shop`、`ollama` 两个目的主机，不需要关闭代理；不要把容器内部地址填成浏览器的 localhost。外部浏览器使用 `localhost:8088`。首次运行确认 Ollama 模型已下载，避免把模型超时的降级结果误认为 AI 正常输出。

## 完整验证样例

使用一个从未用过的代号，例如 `demo_` 加当前时间。每次运行完成后再进行下一次，台词可自由填写。

| 步骤 | 动作/台词 | 预期账本 | 应观察的交互 |
| --- | --- | --- | --- |
| 1 | `prank` / “我把所有价签改成了谜语！” | 第 1 回合，43 金币，信任 -2，下一件 24 | 初次没有历史；店主对恶作剧作出反应 |
| 2 | 关闭测试窗口后重新运行 `visit` / “老板还认得我吗？” | 第 2 回合，43 金币，信任 -2 | 旧账出现第 1 回合恶作剧；模型可引用旧事，而非只靠当前输入 |
| 3 | `apologize` / “请你喝汽水，算我赔礼。” | 第 3 回合，38 金币，信任 0，价格 20 | 旧账仍能看到恶作剧，道歉实际改善关系 |
| 4 | `help` | 第 4 回合，43 金币，信任 1，价格 18 | 帮忙改变下一次购买价格 |
| 5 | `buy` | 第 5 回合，25 金币，背包有会打嗝的月亮 | 金币与物品持久化 |
| 6 | 换全新代号 `visit` | 40 金币，信任 0，空背包，无旧账 | 不泄露另一存档的历史 |

继续帮忙获取金币并购买即可集齐三件物品。信任范围 -5～5，价格最低 12 金币；重复买已集齐的背包不会扣款。模型对白不是唯一标准答案，账本和旧账必须符合实际状态。

### 数据库作用的核查

在 Dify 的运行详情中查看“读取旧账并写入本轮 · seekdb”节点输出，核对 `storage=seekdb`、`state` 和 `memories`。下一轮模型节点输入应包含这些实际历史。

也可进入 seekdb 容器，通过 MySQL 客户端交互输入 `.env` 中的 `SHOP_DB_PASSWORD`，查询：

```sql
USE dify_grudge_shop;
SELECT player_id,turn,coins,trust,inventory FROM players WHERE player_id='你的测试代号';
SELECT turn,action,memory FROM events WHERE player_id='你的测试代号' ORDER BY turn;
```

新代号即可重置体验。无需删表、删除卷或影响其他玩家。若做数据库级清理，只删除明确测试代号对应的两个表中的行。

## 异常与限制

- 模型失败或超时：明确显示“模型暂不可用，使用规则对白”；本轮规则已写入 seekdb，不能把重试整轮当作只重试对白。服务端使用 Dify 本次运行 ID 保证节点内重试幂等；新的一次工作流运行是新回合。
- 数据库/鉴权失败：工作流核验节点停止，不能生成“操作成功”的假账本。
- 模型不直接执行 SQL，也不能更改物品或金钱。用户台词只用于对白，不计入规则操作。当前历史保存全部事件，给模型仅最近四次。
- 这是本地单人/熟人演示：代号不是身份认证，不应公开到互联网作为多用户产品。API token 为服务级鉴权，知道代号且能使用此工作流的人可以继续该存档。
- 0.6B 小模型偶尔不遵循角色或记忆提示，账本始终显示真实结果。可在资源允许时自行改用更强 Ollama 模型并先下载，评审复现默认不依赖该改动。

## 来源

玩法、后端、DSL 和提示词为本作品实现。部署配置从 [Dify 1.9.1 官方源码](https://github.com/langgenius/dify/tree/1.9.1/docker) 按 commit 下载，保留其 LICENSE。参考 [Dify HTTP Request](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)、[seekdb Docker 参数](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)、[Qwen3 模型](https://ollama.com/library/qwen3:0.6b)。不复用其他参赛作品。

## 已完成的实际验证

- `evidence/dify-verification.json`：在真实 Dify 1.9.1 中导入本 DSL，执行恶作剧、回访、道歉、帮忙、购买及全新玩家访问，六次运行均成功。逐轮核对真实 seekdb 的状态与页面账本，没有使用规则对白降级。
- 每次运行记录包含节点状态、输出、持久化状态及 Ollama 的模型名和生成 token 数。通过 HTTP 节点调用模型，因此 Dify 自身的 `total_tokens` 为 0；模型实际生成量以 `ollama_generation.eval_count` 为准。
- `evidence/backend-verification.json`：额外验证鉴权、输入、幂等、并发重复扣款保护、结局与降级。这份记录是后端集成验证，不冒充 Dify 引擎运行。
- 平台分数需另行提交评测确认；本地通过不等同于比赛评分。

### 可选：自动复现完整验证

普通评审可按上述界面步骤运行。若是在新建的本地演示实例，可用下面的脚本代替手动设置账户、导入和测试：

```bash
python scripts/verify_dify.py --setup
```

它只在未初始化的本机 Dify 创建随机演示管理员，将登录信息存入忽略的 `.runtime/dify-admin.local.json`，不会打印密码。再次验证运行 `python scripts/verify_dify.py`，会更新该演示应用并创建新的测试代号。已经初始化的其他实例不会被改写账户；使用已有账户时，可自行在该私密 JSON 中配置 `email`、`password` 后运行。不要提交该文件。

Windows WSL 的 systemd 服务本身未必能保持实例运行。体验期间请保持一个 Ubuntu 终端开启；本地启动后若端口拒绝连接，先启动 Ubuntu，再在 `.runtime/docker` 运行同一 Compose 项目的 `up -d`。不要通过删除卷解决重启问题。
