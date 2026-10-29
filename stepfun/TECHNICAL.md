# 一页技术说明｜本地知识匣

**方案。** 浏览器 Transformers.js 3.8.1 Worker 执行文档和查询 embedding；Node 通过 seekdb-js 1.4.0 存取 seekdb 1.4.0。禁止远程模型推理；浏览器仅取本机文件。

**模型与量化。** Apache-2.0 的 `Xenova/all-MiniLM-L6-v2`，固定 revision `751bff37182d3f1213fa05d7196b954e230abad9`。使用发布方 FP32 / FP16 / INT8 ONNX 产物，下载脚本按官方 hash 校验。不是本项目重新训练或校准。计入 ONNX + 同一套 tokenizer/config：

| 精度 | 实测文件体积（十进制 MB） | FP32/当前体积 |
| --- | ---: | ---: |
| fp32 | 91.33 | 1.000× |
| fp16 | 46.24 | 1.975× |
| q8 | 23.92 | 3.819× |

**固定测试条件。** Windows 11，i7-14700HX，15.7 GiB RAM；Chrome 156.0.8078.73。实际 WebGPU 适配器为 Intel gen-12lp（非 NVIDIA）；WASM 4 线程。24 篇原创英文短文、12 个单相关文档查询，3 次重复，结果缓存关闭；下表为热运行中位数。GPU/WASM 分开比较，不宣称 GPU 必然更快。

| 配置 | 文档/秒 | 查询向量 ms | seekdb ms | 提交至展示 ms | Recall@3 / nDCG@3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| wasm_fp32_b1 | 114.3 | 4.76 | 46.67 | 81.07 | 1.000 / 0.938 |
| wasm_q8_b1 | 140.6 | 3.86 | 49.44 | 81.53 | 1.000 / 0.969 |
| wasm_q8_b8 | 176.8 | 3.82 | 49.32 | 81.23 | 1.000 / 0.969 |
| gpu_fp32_b8 | 87.7 | 10.80 | 47.37 | 81.44 | 1.000 / 0.938 |
| gpu_fp16_b8 | 128.4 | 10.15 | 46.42 | 81.52 | 1.000 / 0.938 |

**质量与优化。** FP16 的 nDCG@3 与 FP32 相同；INT8 在此小集合上高约 0.031，三种精度 Recall@3 均为 1.0，不能外推为通用质量提升。INT8/WASM 批大小 1→8 的吞吐由 140.6→176.8 文档/秒。同样 24 文档由逐篇刷新改为每批刷新，刷新次数 24→1，刷新耗时中位数 15.66→0.55 秒。

**加载与首次请求。** 首个 FP32/WASM 模型加载/初始化 1171.2 ms，其中本机 HTTP 文件获取跨度 356.1 ms；首次文档向量化 306.4 ms。后续模型切换可命中文件缓存，模型对象复用为 0 ms；不能用作公网下载速度对比。每种配置的首次查询、文件获取和热请求原始值另附 JSON。

**数据与状态。** 384 维 cosine HNSW + beng 全文索引，保存原文、标题、精度和模型版本；SHA256 ID + upsert 去重。浏览器查询向量与全文条件传给 `hybridSearch`，由 seekdb `DBMS_HYBRID_SEARCH.GET_SQL` 生成并执行 RRF SQL，应用不融合两路列表。每批 `refresh_index()` 成功后才标记 ready；切换集合清除旧结果，数据库客户端串行化以隔离 session 参数。

**降级与验收。** 优先 WebGPU FP16；适配器无 FP16、初始化失败或推理失败时转 WASM INT8。独立 Chrome `--disable-gpu` 下实际适配器不可用，自动降级后导入/检索通过。增量、去重、集合隔离和重载状态均通过；Edge 界面实测另附。

**复现。** 依 README 安装并启动，运行 `npm run verify` 和 `node scripts/ui_smoke.mjs`。详见 `evidence/browser-benchmark.json`、`model-files.json`、`edge-ui.json` 及截图。测试集规模有限；本地通过不代表比赛得分。
