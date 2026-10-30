"""
研读台 ReadTrace - seekdb 真实集成测试脚本

本脚本连接真实 seekdb 实例执行端到端验证，不使用 mock。
覆盖：导入/重复/项目隔离/搜索/卡片/进度/持久化/非法输入

使用方法:
  1. 确保 seekdb 已启动且 qoder_research 库已初始化
  2. 确保 .env 配置正确（或设置环境变量）
  3. 运行: python scripts/integration_test.py

注意：此脚本会向数据库写入测试数据（使用独立的测试项目），
      运行前请确认不会影响生产数据。
"""
import json
import sys
import os
import time
import hashlib
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = 0
FAIL = 0
ERRORS = []


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append((name, detail))
        print(f"  [FAIL] {name} - {detail}")


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run_tests():
    global PASS, FAIL

    from app.database import get_cursor, check_health, DatabaseError
    from app.services.project import create_project, list_projects, get_project, delete_project
    from app.services.document import import_document, list_documents, get_document, get_document_chunks, delete_document
    from app.services.search import search_documents
    from app.services.card import create_card, list_cards, delete_card, is_card_saved, export_cards_markdown
    from app.services.progress import get_progress, update_progress, get_project_stats

    # ===== 0. 健康检查 =====
    section("0. 数据库健康检查")
    health = check_health()
    report("数据库连接", health["status"] == "ok", str(health))

    if health["status"] != "ok":
        print("\n数据库不可用，终止后续测试。")
        sys.exit(1)

    # ===== 1. 项目隔离 =====
    section("1. 项目创建与隔离")
    p1 = create_project("集成测试项目A", "用于测试项目隔离")
    p2 = create_project("集成测试项目B", "用于测试项目隔离")
    report("创建项目A", p1 is not None and p1["id"] > 0)
    report("创建项目B", p2 is not None and p2["id"] > 0)
    report("项目ID不同", p1["id"] != p2["id"])

    projects = list_projects()
    report("列表包含两个测试项目", len([p for p in projects if p["id"] in (p1["id"], p2["id"])]) == 2)

    try:
        # ===== 2. 文档导入 =====
        section("2. 文档导入与切块")
        content_a = """学习方法概述

主动回忆是一种高效的学习策略。通过在不看笔记的情况下尝试回忆所学内容，可以显著增强记忆效果。研究表明，主动回忆比被动重复阅读更有效。

间隔重复是另一种经过验证的学习方法。将复习分散在多个时间段，而不是集中在一次，可以提高长期记忆保持率。艾宾浩斯遗忘曲线为此提供了理论支持。

交错练习指的是在练习中混合不同类型的问题。虽然这种方法在短期表现上可能不如集中练习，但长期来看能促进更深层的理解和迁移能力。"""

        doc_a = import_document(
            project_id=p1["id"],
            title="学习方法笔记",
            content=content_a,
            author="测试作者",
            source_url="https://example.com/learning",
            tags="学习方法,笔记",
        )
        report("导入文档A", doc_a is not None and doc_a["id"] > 0)
        report("文档标题正确", doc_a["title"] == "学习方法笔记")
        report("文档作者正确", doc_a["author"] == "测试作者")

        chunks_a = get_document_chunks(doc_a["id"])
        report("切块数量 >= 3", len(chunks_a) >= 3, f"实际: {len(chunks_a)}")
        report("段落编号连续", all(chunks_a[i]["paragraph_number"] == i + 1 for i in range(len(chunks_a))))

        expected_hash = hashlib.sha256(content_a.encode("utf-8")).hexdigest()
        report("内容哈希正确", doc_a["content_hash"] == expected_hash)

        # ===== 3. 幂等导入 =====
        section("3. 幂等导入（重复导入同项目相同内容）")
        doc_a_dup = import_document(
            project_id=p1["id"],
            title="学习方法笔记",
            content=content_a,
            author="测试作者",
        )
        report("重复导入返回同一文档", doc_a_dup["id"] == doc_a["id"])

        # ===== 4. 跨项目独立 =====
        section("4. 跨项目独立（相同内容不同项目）")
        doc_b = import_document(
            project_id=p2["id"],
            title="学习方法笔记（项目B副本）",
            content=content_a,
        )
        report("跨项目导入生成新文档", doc_b["id"] != doc_a["id"])
        report("内容哈希相同", doc_b["content_hash"] == doc_a["content_hash"])

        # ===== 5. 更多文档用于搜索 =====
        section("5. 搜索功能验证")
        content_c = """认知科学与学习

大脑的可塑性使得终身学习成为可能。神经科学研究发现，学习新知识时大脑会形成新的突触连接。

多感官学习理论认为，同时使用视觉、听觉和动觉可以增强学习效果。这一理论在教学中有广泛应用。

元认知是指对自身认知过程的认知和监控。培养元认知能力可以帮助学习者更好地规划、监控和评估自己的学习。"""

        doc_c = import_document(
            project_id=p1["id"],
            title="认知科学笔记",
            content=content_c,
            tags="认知科学,脑科学",
        )

        results = search_documents(p1["id"], "学习")
        report("搜索'学习'有结果", len(results) > 0, f"实际: {len(results)}")
        report("搜索结果包含相关性得分", all("relevance" in r for r in results))
        report("搜索结果仅来自本项目A", all(r.get("document_title") in ["学习方法笔记", "认知科学笔记"] for r in results))

        results_b = search_documents(p2["id"], "学习")
        report("项目B搜索不到项目A的文档", len(results_b) == 0 or all(r.get("document_title") != "学习方法笔记" for r in results_b))

        results_specific = search_documents(p1["id"], "间隔重复")
        report("搜索'间隔重复'命中正确段落", len(results_specific) > 0)
        if results_specific:
            report("命中段落包含关键字", "间隔重复" in results_specific[0]["content"])

        empty_results = search_documents(p1["id"], "")
        report("空查询返回空列表", empty_results == [])

        # ===== 6. 证据卡片 =====
        section("6. 证据卡片功能")
        chunk_for_card = chunks_a[0]
        card1 = create_card(p1["id"], doc_a["id"], chunk_for_card["id"], "这是一个重要的学习方法")
        report("创建卡片成功", card1 is not None and card1["id"] > 0)
        report("卡片包含批注", card1.get("annotation") == "这是一个重要的学习方法")
        report("卡片包含出处文档标题", card1.get("document_title") == "学习方法笔记")
        report("卡片包含段落编号", card1.get("paragraph_number") == chunk_for_card["paragraph_number"])

        saved = is_card_saved(p1["id"], doc_a["id"], chunk_for_card["id"])
        report("检查卡片已保存", saved is True)

        cards = list_cards(p1["id"])
        report("列出项目卡片", len(cards) >= 1)

        md = export_cards_markdown(p1["id"], "测试项目")
        report("导出Markdown包含标题", "# 测试项目" in md)
        report("导出Markdown包含出处", "document_id=" in md)
        report("导出Markdown包含段落号", "段落编号" in md)

        # ===== 7. 阅读进度 =====
        section("7. 阅读进度")
        prog = get_progress(doc_a["id"])
        report("新文档默认未读", prog["status"] == "unread")

        updated = update_progress(doc_a["id"], "reading")
        report("更新为在读", updated["status"] == "reading")

        updated2 = update_progress(doc_a["id"], "read")
        report("更新为已读", updated2["status"] == "read")

        stats = get_project_stats(p1["id"])
        report("项目统计包含总数", stats["total"] >= 2)
        report("项目统计包含已读", stats["read"] >= 1)

        # ===== 8. 持久化验证 =====
        section("8. 持久化验证（重新查询确认数据存在）")
        doc_check = get_document(doc_a["id"])
        report("文档持久化存在", doc_check is not None and doc_check["id"] == doc_a["id"])

        cards_check = list_cards(p1["id"])
        report("卡片持久化存在", len(cards_check) >= 1)

        prog_check = get_progress(doc_a["id"])
        report("进度持久化存在", prog_check["status"] == "read")

        # ===== 9. 非法输入 =====
        section("9. 非法输入处理")
        try:
            import_document(project_id=p1["id"], title="空内容测试", content="   ")
            report("拒绝空内容", False, "未抛出异常")
        except ValueError:
            report("拒绝空内容", True)

        try:
            update_progress(doc_a["id"], "invalid_status")
            report("拒绝无效进度状态", False, "未抛出异常")
        except ValueError:
            report("拒绝无效进度状态", True)

    finally:
        # ===== 10. 清理测试数据 =====
        section("10. 清理测试数据")
        try:
            delete_project(p1["id"])
            delete_project(p2["id"])
            report("清理测试项目", True)
        except Exception as e:
            report("清理测试项目", False, str(e))

    # ===== 汇总 =====
    section("测试汇总")
    total = PASS + FAIL
    print(f"\n  总计: {total}  通过: {PASS}  失败: {FAIL}")
    if ERRORS:
        print("\n  失败详情:")
        for name, detail in ERRORS:
            print(f"    - {name}: {detail}")
    print()

    return FAIL == 0


if __name__ == "__main__":
    try:
        success = run_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n测试脚本异常终止: {e}")
        traceback.print_exc()
        sys.exit(2)
