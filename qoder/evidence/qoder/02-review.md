# ReadTrace 第一轮独立验收反馈

继续在同一会话完成修复。所有主要代码仍由你 Qoder 完成；主机负责独立真实运行验证。仍然只使用文件编辑，不运行Shell、不启动服务、不读任何 .env 或工作区外认证文件、不提交Git、不调用其他智能体。本轮暂不发布Sites。

## 主机实际结果

- 数据库真实版本 `5.7.25-OceanBase seekdb-v1.4.0.0`。
- 原版初始化第一句失败 `(1273, "Unknown collation: 'utf8mb4_unicode_ci'")`。主机仅在内存把该排序规则替换成 `utf8mb4_general_ci` 后，所有8条初始化SQL均成功，包括外键CASCADE与ngram全文索引。请修正原文件排序规则；不要无依据删除已经工作的功能。
- 首轮 pytest：39 passed / 5 failed。基本短段落全被过滤；CRLF空行未正确拆分；两个“长段落”测试输入其实没有超过2000字符（应修改测试输入到真正超长，不能盲目降阈值）；大小写HTTPS方案被拒绝。

## 必须修正

1. **部署必须真实seekdb**：当前compose用oceanbase-ce错误。固定 `oceanbase/seekdb:1.4.0-100000172026082615`；env `ROOT_PASSWORD: ${DB_PASSWORD:?Set DB_PASSWORD in .env}`, MEMORY_LIMIT=2G, DATAFILE_SIZE=512M, LOG_DISK_SIZE=2G；持久卷 `/var/lib/seekdb`。只把数据库映射 `127.0.0.1:${DB_PORT:-2896}:2881`。建议compose仅提供DB；app从本机venv运行并只监听127.0.0.1:8036，避免给出未验证的Docker init挂载和错的健康检查。加Python初始化脚本，从settings读取凭据但连接时不预设尚未建立的数据库，执行固定schema。README/env示例所有端口一致，初始化和演示数据命令完整。任何密码仅占位值，补.dockerignore避免COPY .带入.env。
2. **不丢笔记内容**：切块保留所有非空短段落，包括“标题”和“关键点”几字；标准化CRLF/CR，超长段落分割不得吞字符或标点。原文可完整查看，不能仅保存过滤后的摘录。更新有意义的边界测试。
3. **原子导入和真实校验**：现在autocommit=True使事务形同虚设。一个导入中的document/chunks/progress必须共同commit，失败rollback；并发重复导入不能500或留下半文档。服务层统一使用DocumentImport验证(title/content/size/source等)，供JSON、上传和演示脚本复用。拒绝空白项目名/文档标题、不存在的项目、空文件/空白文件、超10MiB、非UTF8、无后缀或错误后缀；上传最多读limit+1避免无界内存。上传也必须URL校验。不要把数据库错误统一伪装输入错误，也不要返回密码/主机SQL细节。
4. **证据关系及批注**：create_card目前可把项目A、文档B、段落C拼在一起，需实际校验project→document→chunk归属，冲突返回4xx。更新批注前端发JSON但后端从query读，必须统一为JSON模型并加5000长度限制；重复提交同一批注也成功。导出必须包括作者、来源URL、段落号、document/chunk ID以及原文批注；多行引文每行加>，用户文本在Markdown中不允许注入可执行HTML/不安全链接。
5. **前端XSS与状态**：escapeHtml只用textNode.innerHTML，不会转义双引号，但你放在href/data-annotation属性中，必须修复属性注入；安全URL用URL解析且只http(s)，推荐DOM setAttribute/textContent。切换项目清空旧搜索/卡片/原文状态并防止旧异步请求覆盖新项目。粘贴模式的required textarea在上传模式需要disabled或移除required，否则浏览器阻止提交。错误422要显示清楚字符串，不显示[object Object]。按钮操作有禁用/加载状态避免重复请求。
6. **准确文档与测试**：DEVLOG只记录实现与上面的主机实际结果；不能说本轮测试通过。首轮集成脚本检查空白文本是重要缺陷，不可把任意Exception都当校验通过；用明确ValueError。持久化重查不是“后端重启测试”，准确标注，主机会做重启。集成脚本要finally清理自己创建的项目，数据库不可用/任何失败退出非0。

完成后报告修改文件与仍需主机运行的检查，不发布，不猜测测试成功。

## 刚完成的真实集成结果（需同时修正）

- 导入/幂等/项目筛选、搜索“学习”、卡片、进度更新成功，但搜索“间隔重复”无结果。主机对同一ngram索引做了只读对照：BOOLEAN MODE的“间隔重复”和“+间隔重复”都是0；NATURAL LANGUAGE MODE的“间隔重复”实际命中4条。因此改用参数化NATURAL LANGUAGE MODE，不人为拼+，不用LIKE或内存兜底伪装全文检索。说明自然语言模式是关键词相关性搜索，不承诺严格AND。
- `get_project_stats`报1064，错误位于 `AS read FROM...`，read是保留字；改用安全别名或反引号并保持API键一致。
- 现有演示笔记包含“实验高出约20%”“平均需要23分钟”等没有来源的效果数字，即便末尾写演示也不需要编造这些统计。改成原创操作说明/自拟学习实验模板，不声称真实研究证明。8篇仍保留可验证关键词。
- load_demo_data单个导入失败仍总返回True，需明确非0退出。
- DEVLOG将每次新建PyMySQL连接描述成“连接池”不准确，改成真实实现说明。
