# 主机独立验收

验收日期：2026-10-30。实现及主要修复由真实Qoder会话完成；以下由Codex独立执行，不是Qoder自行声称通过。

| 项目 | 实际结果 |
|---|---|
| Python逻辑测试 | 45 passed |
| Qoder编写的真实seekdb集成脚本 | 41项通过，0失败 |
| 独立HTTP API验收 | 27项通过，0失败 |
| 独立浏览器操作 | 8项通过，0失败 |
| 后端实际重启 | 文档、批注与阅读状态均保留 |
| 演示资料 | 8/8导入成功，真实FTS查询响应已保存 |
| Qoder Sites匿名访问 | HTTP 200，4张图片加载成功，移动端无横向溢出 |

真实数据库：`5.7.25-OceanBase seekdb-v1.4.0.0`；验收连接本机已有同版本实例，在专属`qoder_research`库运行，没有用SQLite或mock替代数据库。验证了仓库的Python初始化入口；未重新创建Compose容器，也未运行可选Dockerfile构建。Compose镜像与已运行实例版本一致。

API脚本通过FastAPI TestClient访问真实服务路由和真实数据库。并发验收使用4线程。回滚检查只对切块函数注入故障，确认数据库没有遗留半文档；正常流程未mock数据库。

浏览器检查使用独立临时无登录Edge实例，覆盖粘贴导入后刷新列表、中文检索、收藏与文本安全、批注更新、上传、刷新持久化及390px移动布局。`evidence/screenshots/`是实际应用截图，不是设计稿。全部资料和批注明确是合成演示，不作为用户规模或提效证据。

记录：

- [API结果](evidence/api-acceptance.json)
- [页面结果](evidence/browser-acceptance.json)
- [实际重启结果](evidence/restart-verification.json)
- [数据库查询响应](evidence/demo-search.json)
- [Markdown导出](evidence/sample-export.md)
- [公开网站匿名访问](evidence/site-anonymous.json)

网站的匿名访问及最终发布结果另见`SITES_DELIVERY.md`。本文件不宣称已经取得大赛得分。
