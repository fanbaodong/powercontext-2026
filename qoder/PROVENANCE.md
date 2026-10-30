# Qoder开发归属与证据

## 真实开发会话

- 官方工具：Qoder CN CLI 1.1.81，个人账号浏览器授权登录。
- 会话ID：`790ccffd-3e95-44a4-a5ac-d65d4ab5c8c6`。
- 代码基线：官方PowerContext `2026-competition-ecosystem`，基线SHA `d647275c308e09b7b623f322a26f5c4e85734fb0`。
- 本题分支：`competition/26-qoder-research-desk`；项目文件均在`qoder/`。
- 参赛者提供的账号UID：`1918138641918910`。CLI站点元数据未单独返回账号UID，不以头像路径或用户名推测；来源说明见`SITES_DELIVERY.md`。

## 分工

Qoder完成主要架构、数据库表、后端模型/服务/路由、原生前端、演示资料、初始测试文档及Sites展示页；同一真实会话根据主机提供的报错修复了排序规则、镜像、全文查询模式、事务、输入校验、卡片归属、批注更新及Markdown转义。

Codex整理需求、安装环境、连接真实seekdb独立验收；补充了少量验收修复：前端请求序号缺省值、移动端布局、JSON文本字节上限、旧测试引用、长段落边界空白与演示文字审校；整理最终说明、独立验收脚本、截图与Git交付。没有把Codex的工作伪称为Qoder开发。

Qoder的Write工具在覆盖app.js时连续报“File has not been read yet”。主机停止该次重复生成，把该Qoder工具调用中的完整content原样落盘，随后做上述小修复。原样落盘时的SHA256为 `e2947c623b3ea146073a1529e765bea0da3b5aae56062d9dd46fadb39ef40148`；真实失败与修复过程都保留在原始事件流中。

## 提交的记录

- [开发任务原文](evidence/qoder/01-task.md)
- [第一轮真实报错反馈](evidence/qoder/02-review.md)
- [第二轮验收反馈](evidence/qoder/03-review.md)
- [实际对话文本导出](evidence/qoder/conversation.md)
- [工具调用元数据、生成内容摘要及原始日志SHA256](evidence/qoder/session-export.json)
- [对话导出页面截图](evidence/qoder/conversation-export.png)

`conversation-export.png`是实际CLI事件流导出页面的截图，**不是Qoder原生客户端界面截图**。它保留真实输出并明确标注来源，没有伪造对话界面。原始未改写JSONL与CLI运行元数据保存在参赛者本机仓库外，未公开内部思考、认证目录或完整工具环境信息；可按赛事要求进一步核对。

最终功能是否通过以`ACCEPTANCE.md`中的实际验收为准，历史对话的中间状态不代表最终结论。没有伪造用户反馈、真实使用人数或提效统计。
