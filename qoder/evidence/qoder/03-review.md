# 接续修复，不重写已完成代码

你上一轮在app/static/js/app.js上反复Read后Write仍报 `File has not been read yet`，同一错误三次。主机已停止那个工作进程，保留完整记录，并把你最后一次Write工具调用中的完整content原样落盘到该文件，Node --check通过，SHA一致。此文件仍是你Qoder生成，Codex只完成机械落盘。请不要继续重复全量Write；现有文件需改动时优先Read后用Edit，使用工作区相对路径。若再次重复报错，报告具体情况，不无限重试。

现在继续原计划尚未完成的测试、演示数据、README/ARCHITECTURE/VERIFICATION/DEVLOG更新。所有约束保持：不运行Shell，不读取.env/外部私有目录，不启动服务，不委派，不commit/push，暂不发布Sites。

主机对修复后的后端完成27项独立真实seekdb API检查：26通过，1失败。已通过真实中文长词FTS、短段落/CRLF、项目隔离、非法上传/URL/超限拒绝、JSON批注更新、进度统计、4线程并发去重和故障注入回滚无残留。

唯一失败：同一批注重复PUT返回404。update_card_annotation还在用UPDATE rowcount==0判断不存在；MySQL未变数据rowcount为0。请改为先查询该id+project_id是否存在，再更新并返回，或用恰当FOUND_ROWS且仍校验归属，不能把未修改当成404。不要仅改测试。

另两项审查需修正：
1. init_db.py动态插入settings.db_name标识符，限定为qoder_research或添加严格 `[A-Za-z_][A-Za-z0-9_]*` 校验，不能信任任意反引号输入。
2. 导出Markdown仍将chunk_content不经处理放进>引用，可包含HTML或危险Markdown链接。请用正确转义保留原始字符，避免直接删除用户的HTML文本或数学比较符号；标题/作者/批注也要转义而不是正则删除。Markdown文本中的方括号/链接语法、HTML需要安全地作为文本显示。源码中的原始笔记仍完整存储。

提醒原计划仍需做的：纯逻辑测试修正过短的“长段落”fixture；演示笔记去掉20%/23分钟等无来源效果统计；load_demo_data失败不能返回True；集成脚本finally只清理自身项目、禁止任意异常当校验成功；文档部署改成真正seekdb固定镜像+本机8036，并说明真实重启测试由主机后续完成。请不要声称修复后的新测试已经运行。

完成这些后结束本轮并给出实际修改摘要，主机会运行最终验证和补充真实截图。
