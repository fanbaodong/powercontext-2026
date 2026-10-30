(function () {
    "use strict";

    var state = {
        projects: [],
        currentProjectId: null,
        currentTab: "library",
        documents: [],
        searchResults: [],
        cards: [],
        currentDocument: null,
        importMode: "paste",
        requestSeq: 0,
    };

    function nextSeq() {
        state.requestSeq += 1;
        return state.requestSeq;
    }

    function extractError(errBody) {
        if (!errBody) return "请求失败";
        if (typeof errBody.detail === "string") return errBody.detail;
        if (typeof errBody.error === "string") return errBody.error;
        if (Array.isArray(errBody.detail)) {
            return errBody.detail.map(function (e) {
                var loc = (e.loc || []).join(" -> ");
                return loc + ": " + e.msg;
            }).join("; ");
        }
        try { return JSON.stringify(errBody); } catch (x) { return "请求失败"; }
    }

    var api = {
        _err: function (res, body) {
            var e = new Error(extractError(body));
            e.status = res.status;
            return e;
        },
        get: async function (url) {
            var res = await fetch(url);
            if (!res.ok) throw api._err(res, await res.json().catch(function () { return { detail: res.statusText }; }));
            return res.json();
        },
        post: async function (url, body) {
            var res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
            if (!res.ok) throw api._err(res, await res.json().catch(function () { return { detail: res.statusText }; }));
            return res.json();
        },
        put: async function (url, body) {
            var res = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
            if (!res.ok) throw api._err(res, await res.json().catch(function () { return { detail: res.statusText }; }));
            return res.json();
        },
        del: async function (url) {
            var res = await fetch(url, { method: "DELETE" });
            if (!res.ok) throw api._err(res, await res.json().catch(function () { return { detail: res.statusText }; }));
            return res.json();
        },
        upload: async function (url, formData) {
            var res = await fetch(url, { method: "POST", body: formData });
            if (!res.ok) throw api._err(res, await res.json().catch(function () { return { detail: res.statusText }; }));
            return res.json();
        },
    };

    function showToast(message, type) {
        type = type || "info";
        var container = document.getElementById("toast-container");
        var toast = document.createElement("div");
        toast.className = "toast " + type;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(function () { toast.remove(); }, 3000);
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#x27;");
    }

    function safeUrl(url) {
        if (!url) return null;
        try {
            var parsed = new URL(url);
            if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
            return parsed.href;
        } catch (e) { return null; }
    }

    function setButtonLoading(btn, loading) {
        if (!btn) return;
        if (loading) {
            btn.disabled = true;
            btn._origText = btn.textContent;
            btn.textContent = "处理中...";
        } else {
            btn.disabled = false;
            if (btn._origText) btn.textContent = btn._origText;
        }
    }

    async function checkHealth() {
        var indicator = document.getElementById("health-indicator");
        try {
            var data = await api.get("/api/health");
            if (data.status === "ok") {
                indicator.className = "health-indicator ok";
                indicator.querySelector(".text").textContent = "seekdb 已连接";
            } else {
                indicator.className = "health-indicator error";
                indicator.querySelector(".text").textContent = "数据库不可用";
            }
        } catch (e) {
            indicator.className = "health-indicator error";
            indicator.querySelector(".text").textContent = "数据库不可用";
        }
    }

    async function loadProjects() {
        try {
            state.projects = await api.get("/api/projects");
            renderProjects();
        } catch (e) {
            showToast("加载项目失败: " + e.message, "error");
        }
    }

    function renderProjects() {
        var list = document.getElementById("project-list");
        list.innerHTML = "";
        if (state.projects.length === 0) {
            list.textContent = "暂无项目，点击上方 + 创建";
            return;
        }
        state.projects.forEach(function (p) {
            var item = document.createElement("div");
            item.className = "project-item" + (p.id === state.currentProjectId ? " active" : "");
            var nameSpan = document.createElement("span");
            nameSpan.className = "name";
            nameSpan.textContent = p.name;
            var countSpan = document.createElement("span");
            countSpan.className = "count";
            countSpan.textContent = p.document_count + " 篇";
            item.appendChild(nameSpan);
            item.appendChild(countSpan);
            item.addEventListener("click", function () { selectProject(p.id); });
            list.appendChild(item);
        });
    }

    function clearProjectState() {
        state.documents = [];
        state.searchResults = [];
        state.cards = [];
        state.currentDocument = null;
        document.getElementById("search-input").value = "";
        document.getElementById("search-results").textContent = "";
        var hint = document.getElementById("search-hint");
        hint.style.display = "block";
        hint.textContent = "输入关键字后按回车或点击搜索，仅在本项目范围内检索原文段落";
        document.getElementById("document-list").textContent = "";
        document.getElementById("card-list").textContent = "";
        document.getElementById("detail-chunks").textContent = "";
        document.getElementById("detail-meta").textContent = "";
    }

    async function selectProject(projectId) {
        var seq = nextSeq();
        state.currentProjectId = projectId;
        clearProjectState();
        renderProjects();
        document.getElementById("empty-state").style.display = "none";
        document.getElementById("tab-nav").style.display = "flex";
        switchTab("library");
        await loadDocuments(seq);
        await loadStats(seq);
    }

    async function loadStats(seq) {
        if (seq === undefined) seq = state.requestSeq;
        if (!state.currentProjectId) return;
        if (seq !== undefined && seq !== state.requestSeq) return;
        var section = document.getElementById("stats-section");
        section.style.display = "block";
        try {
            var stats = await api.get("/api/progress/project/" + state.currentProjectId + "/stats");
            if (seq !== undefined && seq !== state.requestSeq) return;
            var panel = document.getElementById("stats-panel");
            panel.textContent = "";
            [["总计", stats.total, ""], ["未读", stats.unread, ""], ["在读", stats.reading, " accent"], ["已读", stats.read, ""]].forEach(function (r) {
                var div = document.createElement("div");
                div.className = "stat-row";
                var label = document.createElement("span");
                label.className = "label";
                label.textContent = r[0];
                var value = document.createElement("span");
                value.className = "value" + r[2];
                value.textContent = String(r[1]);
                div.appendChild(label);
                div.appendChild(value);
                panel.appendChild(div);
            });
        } catch (e) {
            document.getElementById("stats-panel").textContent = "加载统计失败";
        }
    }

    function switchTab(tabName) {
        state.currentTab = tabName;
        document.querySelectorAll(".tab-btn").forEach(function (btn) {
            btn.classList.toggle("active", btn.getAttribute("data-tab") === tabName);
        });
        ["library", "search", "cards", "detail"].forEach(function (t) {
            var el = document.getElementById("tab-" + t);
            if (el) el.style.display = t === tabName ? "block" : "none";
        });
        if (tabName === "library") loadDocuments();
        else if (tabName === "cards") loadCards();
    }

    async function loadDocuments(seq) {
        if (seq === undefined) seq = state.requestSeq;
        if (!state.currentProjectId) return;
        if (seq !== undefined && seq !== state.requestSeq) return;
        var list = document.getElementById("document-list");
        list.textContent = "加载中...";
        try {
            var docs = await api.get("/api/documents/project/" + state.currentProjectId);
            if (seq !== state.requestSeq) return;
            state.documents = docs;
            renderDocuments();
        } catch (e) {
            if (seq !== state.requestSeq) return;
            list.textContent = "加载失败: " + e.message;
        }
    }

    function renderDocuments() {
        var list = document.getElementById("document-list");
        list.innerHTML = "";
        if (state.documents.length === 0) {
            list.textContent = "文库为空，点击「导入资料」添加第一篇笔记";
            return;
        }
        var statusTextMap = { unread: "未读", reading: "在读", read: "已读" };
        state.documents.forEach(function (doc) {
            var card = document.createElement("div");
            card.className = "document-card";
            var header = document.createElement("div");
            header.className = "doc-header";
            var titleSpan = document.createElement("span");
            titleSpan.className = "doc-title";
            titleSpan.textContent = doc.title;
            var statusClass = doc.reading_status || "unread";
            var statusSpan = document.createElement("span");
            statusSpan.className = "doc-status " + statusClass;
            statusSpan.textContent = statusTextMap[statusClass] || "未读";
            header.appendChild(titleSpan);
            header.appendChild(statusSpan);
            card.appendChild(header);

            var meta = document.createElement("div");
            meta.className = "doc-meta";
            if (doc.author) { var s = document.createElement("span"); s.textContent = "作者: " + doc.author; meta.appendChild(s); }
            var cs = document.createElement("span"); cs.textContent = doc.char_count + " 字"; meta.appendChild(cs);
            var ks = document.createElement("span"); ks.textContent = doc.chunk_count + " 段"; meta.appendChild(ks);
            var su = safeUrl(doc.source_url);
            if (su) { var sp = document.createElement("span"); var a = document.createElement("a"); a.setAttribute("href", su); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer"); a.textContent = "来源"; sp.appendChild(a); meta.appendChild(sp); }
            if (doc.card_count > 0) { var ccs = document.createElement("span"); ccs.style.color = "var(--accent)"; ccs.textContent = doc.card_count + " 张卡片"; meta.appendChild(ccs); }
            card.appendChild(meta);

            if (doc.tags) {
                var tagsDiv = document.createElement("div");
                tagsDiv.className = "doc-tags";
                doc.tags.split(",").filter(Boolean).forEach(function (t) {
                    var tag = document.createElement("span"); tag.className = "tag"; tag.textContent = t.trim(); tagsDiv.appendChild(tag);
                });
                card.appendChild(tagsDiv);
            }
            card.addEventListener("click", function () { openDocument(doc.id); });
            list.appendChild(card);
        });
    }

    async function openDocument(docId) {
        try {
            var doc = await api.get("/api/documents/" + docId);
            var chunks = await api.get("/api/documents/" + docId + "/chunks");
            state.currentDocument = { doc: doc, chunks: chunks };
            renderDocumentDetail();
            switchTab("detail");
        } catch (e) { showToast("打开文档失败: " + e.message, "error"); }
    }

    function renderDocumentDetail() {
        var d = state.currentDocument.doc;
        var chunks = state.currentDocument.chunks;
        document.getElementById("detail-title").textContent = d.title;
        document.getElementById("detail-progress").value = d.reading_status || "unread";

        var metaEl = document.getElementById("detail-meta");
        metaEl.textContent = "";
        function addMeta(label, value) {
            var row = document.createElement("div"); row.className = "meta-row";
            var l = document.createElement("span"); l.className = "meta-label"; l.textContent = label;
            var v = document.createElement("span"); v.className = "meta-value";
            if (typeof value === "string") v.textContent = value; else v.appendChild(value);
            row.appendChild(l); row.appendChild(v); metaEl.appendChild(row);
        }
        if (d.author) addMeta("作者", d.author);
        var su = safeUrl(d.source_url);
        if (su) { var a = document.createElement("a"); a.setAttribute("href", su); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer"); a.textContent = su; addMeta("来源", a); }
        if (d.tags) addMeta("标签", d.tags);
        addMeta("字数", String(d.char_count));
        addMeta("段落", String(d.chunk_count));

        var chunksEl = document.getElementById("detail-chunks");
        chunksEl.textContent = "";
        chunks.forEach(function (c) {
            var item = document.createElement("div"); item.className = "chunk-item";
            var header = document.createElement("div"); header.className = "chunk-header";
            var numSpan = document.createElement("span"); numSpan.className = "chunk-number"; numSpan.textContent = "第 " + c.paragraph_number + " 段";
            var actionsDiv = document.createElement("div"); actionsDiv.className = "chunk-actions";
            var saveBtn = document.createElement("button"); saveBtn.className = "btn-small btn-primary"; saveBtn.textContent = "收藏为卡片";
            saveBtn.addEventListener("click", function (e) { e.stopPropagation(); openAnnotationModal(d.id, c.id); });
            actionsDiv.appendChild(saveBtn); header.appendChild(numSpan); header.appendChild(actionsDiv); item.appendChild(header);
            var contentDiv = document.createElement("div"); contentDiv.className = "chunk-content"; contentDiv.textContent = c.content;
            item.appendChild(contentDiv); chunksEl.appendChild(item);
        });
    }

    async function doSearch() {
        var query = document.getElementById("search-input").value.trim();
        var hint = document.getElementById("search-hint");
        var results = document.getElementById("search-results");
        if (!query) { hint.style.display = "block"; hint.textContent = "请输入关键字后再搜索"; results.textContent = ""; return; }
        if (!state.currentProjectId) { showToast("请先选择项目", "error"); return; }
        var seq = nextSeq();
        hint.style.display = "none";
        results.textContent = "搜索中...";
        try {
            var data = await api.get("/api/search/project/" + state.currentProjectId + "?q=" + encodeURIComponent(query));
            if (seq !== state.requestSeq) return;
            state.searchResults = data.results || [];
            renderSearchResults(query);
        } catch (e) { if (seq !== state.requestSeq) return; results.textContent = "搜索失败: " + e.message; }
    }

    function renderSearchResults(query) {
        var results = document.getElementById("search-results");
        results.textContent = "";
        if (state.searchResults.length === 0) { results.textContent = "未找到与「" + query + "」匹配的结果"; return; }
        var summary = document.createElement("div");
        summary.style.cssText = "margin-bottom:12px;font-size:13px;color:#636e72";
        summary.textContent = "找到 " + state.searchResults.length + " 条结果（基于 seekdb FULLTEXT 自然语言检索）";
        results.appendChild(summary);
        state.searchResults.forEach(function (r) {
            var item = document.createElement("div"); item.className = "search-result-item";
            var header = document.createElement("div"); header.className = "result-header";
            var ts = document.createElement("span"); ts.className = "result-title"; ts.textContent = r.document_title;
            var ss = document.createElement("span"); ss.className = "result-score"; ss.textContent = "相关度 " + (parseFloat(r.relevance) || 0).toFixed(2);
            header.appendChild(ts); header.appendChild(ss); item.appendChild(header);
            var pi = document.createElement("div"); pi.className = "result-paragraph";
            pi.textContent = "第 " + r.paragraph_number + " 段" + (r.document_author ? " · 作者: " + r.document_author : "");
            var su = safeUrl(r.document_source_url);
            if (su) { pi.appendChild(document.createTextNode(" · ")); var a = document.createElement("a"); a.setAttribute("href", su); a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer"); a.textContent = "来源"; pi.appendChild(a); }
            item.appendChild(pi);
            var cd = document.createElement("div"); cd.className = "result-content"; cd.textContent = r.content; item.appendChild(cd);
            var ad = document.createElement("div"); ad.className = "result-actions";
            var sb = document.createElement("button"); sb.className = "btn-small btn-primary"; sb.textContent = "收藏为卡片";
            sb.addEventListener("click", function () { openAnnotationModal(r.document_id, r.chunk_id); });
            var vb = document.createElement("button"); vb.className = "btn-small btn-secondary"; vb.textContent = "查看原文";
            vb.addEventListener("click", function () { openDocument(r.document_id); });
            ad.appendChild(sb); ad.appendChild(vb); item.appendChild(ad);
            results.appendChild(item);
        });
    }

    async function loadCards() {
        if (!state.currentProjectId) return;
        var seq = nextSeq();
        var list = document.getElementById("card-list");
        list.textContent = "加载中...";
        try {
            var cards = await api.get("/api/cards/project/" + state.currentProjectId);
            if (seq !== state.requestSeq) return;
            state.cards = cards;
            renderCards();
        } catch (e) { if (seq !== state.requestSeq) return; list.textContent = "加载卡片失败: " + e.message; }
    }

    function renderCards() {
        var list = document.getElementById("card-list");
        list.innerHTML = "";
        if (state.cards.length === 0) { list.textContent = "暂无证据卡片。在原文或搜索结果中点击「收藏为卡片」添加。"; return; }
        state.cards.forEach(function (c) {
            var card = document.createElement("div"); card.className = "evidence-card";
            var header = document.createElement("div"); header.className = "card-header";
            var hl = document.createElement("div");
            var ss = document.createElement("span"); ss.className = "card-source"; ss.textContent = c.document_title;
            var ps = document.createElement("span"); ps.className = "card-paragraph"; ps.textContent = " · 第 " + c.paragraph_number + " 段";
            hl.appendChild(ss); hl.appendChild(ps); header.appendChild(hl); card.appendChild(header);
            var cd = document.createElement("div"); cd.className = "card-content"; cd.textContent = c.chunk_content; card.appendChild(cd);
            if (c.annotation) { var ad = document.createElement("div"); ad.className = "card-annotation"; ad.textContent = c.annotation; card.appendChild(ad); }
            var actions = document.createElement("div"); actions.className = "card-actions";
            var eb = document.createElement("button"); eb.className = "btn-small btn-secondary"; eb.textContent = "编辑批注";
            eb.addEventListener("click", function () { promptAnnotation(c.id, c.annotation || ""); });
            var db = document.createElement("button"); db.className = "btn-small btn-danger"; db.textContent = "取消收藏";
            db.addEventListener("click", async function () {
                setButtonLoading(db, true);
                try { await api.del("/api/cards/" + c.id + "?project_id=" + state.currentProjectId); showToast("已取消收藏", "success"); loadCards(); loadStats(); loadProjects(); }
                catch (e) { showToast("操作失败: " + e.message, "error"); }
                finally { setButtonLoading(db, false); }
            });
            actions.appendChild(eb); actions.appendChild(db); card.appendChild(actions);
            list.appendChild(card);
        });
    }

    async function promptAnnotation(cardId, currentAnnotation) {
        var newAnno = prompt("编辑批注:", currentAnnotation);
        if (newAnno === null) return;
        try {
            await api.put("/api/cards/" + cardId, { project_id: state.currentProjectId, annotation: newAnno || null });
            showToast("批注已更新", "success");
            loadCards();
        } catch (e) { showToast("更新失败: " + e.message, "error"); }
    }

    async function exportMarkdown() {
        if (!state.currentProjectId) return;
        var project = state.projects.find(function (p) { return p.id === state.currentProjectId; });
        var name = project ? project.name : "研究项目";
        var btn = document.getElementById("btn-export");
        setButtonLoading(btn, true);
        try {
            var data = await api.get("/api/cards/project/" + state.currentProjectId + "/export?project_name=" + encodeURIComponent(name));
            var blob = new Blob([data.markdown], { type: "text/markdown;charset=utf-8" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a"); a.setAttribute("href", url); a.setAttribute("download", name + "_证据卡片.md");
            a.click(); URL.revokeObjectURL(url);
            showToast("导出成功", "success");
        } catch (e) { showToast("导出失败: " + e.message, "error"); }
        finally { setButtonLoading(btn, false); }
    }

    function openModal(id) { document.getElementById(id).style.display = "flex"; }
    function closeModal(id) { document.getElementById(id).style.display = "none"; }
    function openAnnotationModal(docId, chunkId) {
        document.getElementById("anno-doc-id").value = String(docId);
        document.getElementById("anno-chunk-id").value = String(chunkId);
        document.getElementById("anno-text").value = "";
        openModal("modal-annotation");
    }

    async function updateDocumentProgress(docId, statusVal) {
        try { await api.put("/api/progress/document/" + docId, { status: statusVal }); showToast("进度已更新", "success"); loadStats(); loadProjects(); }
        catch (e) { showToast("更新进度失败: " + e.message, "error"); }
    }

    function setImportMode(mode) {
        state.importMode = mode;
        var textarea = document.getElementById("import-textarea");
        var fileInput = document.getElementById("import-file");
        document.querySelectorAll(".import-tab").forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-mode") === mode); });
        if (mode === "paste") {
            textarea.style.display = "block"; textarea.required = true; textarea.disabled = false;
            fileInput.style.display = "none"; fileInput.required = false; fileInput.disabled = true;
        } else {
            textarea.style.display = "none"; textarea.required = false; textarea.disabled = true;
            fileInput.style.display = "block"; fileInput.required = true; fileInput.disabled = false;
        }
    }

    function init() {
        checkHealth();
        loadProjects();
        document.querySelectorAll(".tab-btn").forEach(function (btn) { btn.addEventListener("click", function () { switchTab(btn.getAttribute("data-tab")); }); });
        document.getElementById("btn-new-project").addEventListener("click", function () { openModal("modal-project"); });
        document.getElementById("btn-import").addEventListener("click", function () {
            if (!state.currentProjectId) { showToast("请先选择项目", "error"); return; }
            document.getElementById("form-import").reset();
            setImportMode("paste");
            openModal("modal-import");
        });
        document.querySelectorAll(".import-tab").forEach(function (tab) { tab.addEventListener("click", function () { setImportMode(tab.getAttribute("data-mode")); }); });
        document.getElementById("btn-search").addEventListener("click", doSearch);
        document.getElementById("search-input").addEventListener("keydown", function (e) { if (e.key === "Enter") doSearch(); });
        document.getElementById("btn-export").addEventListener("click", exportMarkdown);
        document.getElementById("btn-back").addEventListener("click", function () { switchTab("library"); });
        document.getElementById("detail-progress").addEventListener("change", function () { if (state.currentDocument) updateDocumentProgress(state.currentDocument.doc.id, this.value); });
        document.querySelectorAll("[data-close]").forEach(function (btn) { btn.addEventListener("click", function () { closeModal(btn.getAttribute("data-close")); }); });
        document.querySelectorAll(".modal-overlay").forEach(function (overlay) { overlay.addEventListener("click", function () { overlay.parentElement.style.display = "none"; }); });

        document.getElementById("form-project").addEventListener("submit", async function (e) {
            e.preventDefault();
            var submitBtn = this.querySelector('[type="submit"]');
            setButtonLoading(submitBtn, true);
            var formData = new FormData(this);
            try {
                var project = await api.post("/api/projects", { name: formData.get("name"), description: formData.get("description") || null });
                showToast("项目已创建", "success");
                closeModal("modal-project");
                await loadProjects();
                selectProject(project.id);
            } catch (err) { showToast("创建失败: " + err.message, "error"); }
            finally { setButtonLoading(submitBtn, false); }
        });

        document.getElementById("form-import").addEventListener("submit", async function (e) {
            e.preventDefault();
            if (!state.currentProjectId) return;
            var submitBtn = this.querySelector('[type="submit"]');
            setButtonLoading(submitBtn, true);
            try {
                if (state.importMode === "paste") {
                    var formData = new FormData(this);
                    await api.post("/api/documents", {
                        project_id: state.currentProjectId,
                        title: formData.get("title"),
                        author: formData.get("author") || null,
                        source_url: formData.get("source_url") || null,
                        tags: formData.get("tags") || null,
                        content: formData.get("content"),
                    });
                } else {
                    var fileInput = document.getElementById("import-file");
                    if (!fileInput.files.length) { showToast("请选择文件", "error"); return; }
                    var form = this;
                    var fd = new FormData();
                    fd.append("file", fileInput.files[0]);
                    fd.append("project_id", String(state.currentProjectId));
                    fd.append("title", form.querySelector('[name="title"]').value);
                    fd.append("author", form.querySelector('[name="author"]').value || "");
                    fd.append("source_url", form.querySelector('[name="source_url"]').value || "");
                    fd.append("tags", form.querySelector('[name="tags"]').value || "");
                    await api.upload("/api/documents/upload", fd);
                }
                showToast("导入成功", "success");
                closeModal("modal-import");
                await loadDocuments(); await loadStats(); await loadProjects();
            } catch (err) { showToast("导入失败: " + err.message, "error"); }
            finally { setButtonLoading(submitBtn, false); }
        });

        document.getElementById("form-annotation").addEventListener("submit", async function (e) {
            e.preventDefault();
            var submitBtn = this.querySelector('[type="submit"]');
            setButtonLoading(submitBtn, true);
            var docId = parseInt(document.getElementById("anno-doc-id").value);
            var chunkId = parseInt(document.getElementById("anno-chunk-id").value);
            var annotation = document.getElementById("anno-text").value || null;
            try {
                await api.post("/api/cards", { project_id: state.currentProjectId, document_id: docId, chunk_id: chunkId, annotation: annotation });
                showToast("卡片已收藏", "success");
                closeModal("modal-annotation");
                if (state.currentTab === "cards") loadCards();
                loadStats(); loadProjects();
            } catch (err) { showToast("收藏失败: " + err.message, "error"); }
            finally { setButtonLoading(submitBtn, false); }
        });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
