(function () {
  "use strict";

  var state = { overview: null, health: null, view: "overview" };
  var root = document.getElementById("view-root");
  var title = document.getElementById("page-title");
  var message = document.getElementById("global-message");
  var connection = document.getElementById("connection-status");
  var refreshTime = document.getElementById("refresh-time");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function showMessage(kind, text, detail) {
    clear(message);
    message.className = "message-area " + (kind || "");
    if (text) message.appendChild(el("div", "message-text", text));
    if (detail) {
      var details = document.createElement("details");
      details.appendChild(el("summary", "details-summary", "查看技术详情"));
      details.appendChild(el("pre", "technical-detail", detail));
      message.appendChild(details);
    }
  }

  function setLoading(text) {
    clear(root);
    root.appendChild(el("div", "loading-state", text || "正在读取 Remote MCP 数据…"));
  }

  async function jsonRequest(path, options, fallbackMessage) {
    var response = await fetch(path, options || { headers: { "Accept": "application/json" } });
    var data;
    try { data = await response.json(); } catch (error) { throw new Error("本地 API 返回了无法读取的 JSON。"); }
    if (!response.ok || data.status === "error") {
      var err = new Error(data.message || fallbackMessage || "本地 API 请求失败。");
      err.detail = data.detail || "HTTP " + response.status;
      throw err;
    }
    return data;
  }

  function getJson(path) { return jsonRequest(path, { headers: { "Accept": "application/json" } }, "读取本地数据失败。"); }

  function postJson(path, payload) {
    return jsonRequest(path, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }, "提交请求失败。");
  }

  function addField(form, labelText, name, type, required, value) {
    var label = el("label", "form-field");
    label.appendChild(el("span", "form-label", labelText + (required ? " *" : "")));
    var input = document.createElement(type === "textarea" ? "textarea" : type === "select" ? "select" : "input");
    input.name = name; input.required = !!required; input.value = value || "";
    if (type !== "textarea" && type !== "select") input.type = type;
    label.appendChild(input); form.appendChild(label); return input;
  }

  function addSelect(form, labelText, name, options, required, value, labels) {
    var select = addField(form, labelText, name, "select", required, "");
    options.forEach(function (option) {
      var item = el("option", "", labels && labels[option] ? labels[option] : option);
      item.value = option; if (option === value) item.selected = true; select.appendChild(item);
    });
    return select;
  }

  function writeButton(form, labelText) {
    var actions = el("div", "form-actions");
    var button = el("button", "button button-primary", labelText); button.type = "submit"; actions.appendChild(button); form.appendChild(actions); return button;
  }

  function lines(value) { return String(value || "").split(/\r?\n/).map(function (item) { return item.trim(); }).filter(Boolean); }

  async function refreshOverviewData() {
    state.health = await getJson("/api/health");
    state.overview = await getJson("/api/overview");
    updateConnection(state.health);
    refreshTime.textContent = "刷新于 " + new Date().toLocaleTimeString();
  }

  async function submitWrite(form, button, path, payload, confirmation, resultNode, afterSuccess) {
    if (!window.confirm(confirmation + "\n\n提交内容：\n" + JSON.stringify(payload, null, 2))) return;
    button.disabled = true; button.textContent = "正在保存…"; showMessage("", "");
    try {
      var result = await postJson(path, payload);
      clear(resultNode); resultNode.className = "write-result is-success";
      resultNode.appendChild(el("strong", "", "保存成功"));
      resultNode.appendChild(el("pre", "result-json", JSON.stringify(result, null, 2)));
      if (afterSuccess) await afterSuccess();
    } catch (error) {
      showMessage("is-error", error.message, error.detail); clear(resultNode);
    } finally { button.disabled = false; button.textContent = button.getAttribute("data-label") || "保存"; }
  }

  function updateConnection(health) {
    clear(connection);
    var dot = el("span", "status-dot");
    var label;
    if (!health || !health.remote_mcp || !health.remote_mcp.configured) {
      connection.className = "connection-status is-warning"; label = "Cloud MCP 未配置";
    } else if (health.remote_mcp.reachable) {
      connection.className = "connection-status is-online"; label = "Cloud MCP 已连接";
    } else {
      connection.className = "connection-status is-error"; label = "Cloud MCP 不可用";
    }
    connection.appendChild(dot); connection.appendChild(el("span", "", label));
  }

  function valueOrEmpty(value, empty) { return value === null || value === undefined || value === "" ? (empty || "—") : value; }

  function listBlock(items, emptyText) {
    var wrap = el("div", "list-block");
    if (!items || !items.length) { wrap.appendChild(el("p", "empty-inline", emptyText || "暂无记录")); return wrap; }
    items.forEach(function (item) { wrap.appendChild(el("div", "list-row", item)); }); return wrap;
  }

  function card(label, value, accent) {
    var item = el("div", "metric-card" + (accent ? " " + accent : ""));
    item.appendChild(el("div", "metric-label", label)); item.appendChild(el("div", "metric-value", valueOrEmpty(value, "0"))); return item;
  }

  function panel(titleText, kicker) {
    var p = el("article", "panel"); var head = el("div", "panel-header");
    head.appendChild(el("h2", "panel-title", titleText)); if (kicker) head.appendChild(el("span", "panel-kicker", kicker));
    p.appendChild(head); return p;
  }

  function badge(status) { return el("span", "badge badge-" + String(status || "unknown"), status || "unknown"); }

  function renderOverview(data) {
    var status = data.project_status || {}; var tasks = data.tasks || {};
    var candidateSummary = data.candidate_summary || {}; var knowledge = data.project_knowledge || {};
    var brief = data.latest_intelligence_brief; clear(root);
    var hero = el("section", "project-hero"); var heroCopy = el("div", "hero-copy");
    heroCopy.appendChild(el("p", "eyebrow", "当前项目")); heroCopy.appendChild(el("h2", "project-name", data.project_name));
    heroCopy.appendChild(el("p", "project-stage", "阶段 · " + valueOrEmpty(data.current_stage, "尚未记录")));
    hero.appendChild(heroCopy); hero.appendChild(el("div", "hero-source", "来源：Remote MCP\n持久化业务视图")); root.appendChild(hero);
    var metrics = el("section", "metric-grid");
    metrics.appendChild(card("待办任务", tasks.count, "accent-blue")); metrics.appendChild(card("风险", (data.risks || []).length, "accent-amber"));
    metrics.appendChild(card("候选情报", candidateSummary.count, "accent-purple")); metrics.appendChild(card("Project Knowledge", knowledge.count, "accent-green")); root.appendChild(metrics);
    var quick = el("section", "quick-actions"); quick.appendChild(el("span", "quick-label", "快捷操作"));
    [["记录导师要求", "advisor"], ["记录科研活动", "activities"], ["生成阶段报告", "reports"]].forEach(function (action) {
      var actionButton = el("button", "button button-secondary", action[0]); actionButton.type = "button"; actionButton.addEventListener("click", function () { navigate(action[1]); }); quick.appendChild(actionButton);
    }); root.appendChild(quick);
    var grid = el("section", "content-grid");
    var taskPanel = panel("当前任务", "待办"); taskPanel.appendChild(listBlock(tasks.pending, "暂无待处理任务。")); grid.appendChild(taskPanel);
    var riskPanel = panel("风险", "当前"); riskPanel.appendChild(listBlock(data.risks, "暂无已记录风险。")); grid.appendChild(riskPanel);
    var decisionPanel = panel("重要决策", "已记录"); decisionPanel.appendChild(listBlock(data.decisions, "暂无已记录决策。")); grid.appendChild(decisionPanel);
    var activityPanel = panel("最近科研活动", "REMOTE MCP 上下文"); var activities = data.recent_activities || [];
    if (!activities.length) activityPanel.appendChild(el("p", "empty-inline", "暂无活动记录。"));
    activities.slice(0, 5).forEach(function (activity) { var row = el("div", "timeline-row"); row.appendChild(el("div", "timeline-date", valueOrEmpty(activity.date, "—"))); var body = el("div", "timeline-body"); body.appendChild(el("strong", "timeline-title", valueOrEmpty(activity.title, "未命名活动"))); body.appendChild(el("p", "timeline-description", valueOrEmpty(activity.description, "暂无描述"))); row.appendChild(body); activityPanel.appendChild(row); }); grid.appendChild(activityPanel); root.appendChild(grid);
    var lower = el("section", "content-grid lower-grid"); var advisorPanel = panel("最新导师要求", "导师"); var advisor = data.latest_advisor_instruction;
    if (!advisor) advisorPanel.appendChild(el("p", "empty-inline", "暂无导师要求。")); else { advisorPanel.appendChild(el("p", "instruction-text", valueOrEmpty(advisor.instruction, "暂无内容"))); var meta = el("div", "meta-line"); meta.appendChild(badge(advisor.priority)); meta.appendChild(el("span", "", "任务：" + valueOrEmpty(advisor.task, "—"))); advisorPanel.appendChild(meta); }
    lower.appendChild(advisorPanel); var briefPanel = panel("最新科研情报 Brief", "持久化产物");
    if (!brief) briefPanel.appendChild(el("p", "empty-inline", "暂无科研情报 Brief。")); else { briefPanel.appendChild(el("h3", "brief-title", valueOrEmpty(brief.title, "未命名 Brief"))); briefPanel.appendChild(el("p", "brief-summary", valueOrEmpty(brief.executive_summary, "暂无摘要"))); var briefLink = el("button", "text-button", "打开科研情报 →"); briefLink.addEventListener("click", function () { navigate("intelligence"); }); briefPanel.appendChild(briefLink); }
    lower.appendChild(briefPanel); root.appendChild(lower);
  }

  function appendInlineMarkdown(parent, source) {
    var pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
    var text = String(source || ""); var cursor = 0; var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) parent.appendChild(el("span", "", text.slice(cursor, match.index)));
      var token = match[0];
      if (token.indexOf("**") === 0) parent.appendChild(el("strong", "", token.slice(2, -2)));
      else if (token.indexOf("`") === 0) parent.appendChild(el("code", "", token.slice(1, -1)));
      else { var link = el("a", "", token.slice(1, token.indexOf("]("))); var url = match[2]; link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; parent.appendChild(link); }
      cursor = match.index + token.length;
    }
    if (cursor < text.length) parent.appendChild(el("span", "", text.slice(cursor)));
  }

  function renderMarkdown(source) {
    var container = el("div", "markdown-rendered"); var lines = String(source || "").replace(/\r\n?/g, "\n").split("\n"); var index = 0;
    while (index < lines.length) {
      var line = lines[index]; if (!line.trim()) { index += 1; continue; }
      var heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) { var headingNode = el("h" + heading[1].length, "", ""); appendInlineMarkdown(headingNode, heading[2]); container.appendChild(headingNode); index += 1; continue; }
      if (/^\s*---+\s*$/.test(line)) { container.appendChild(document.createElement("hr")); index += 1; continue; }
      if (/^>\s?/.test(line)) { var quote = el("blockquote", "", ""); while (index < lines.length && /^>\s?/.test(lines[index])) { appendInlineMarkdown(quote, lines[index].replace(/^>\s?/, "")); if (index + 1 < lines.length && /^>\s?/.test(lines[index + 1])) quote.appendChild(document.createElement("br")); index += 1; } container.appendChild(quote); continue; }
      var unordered = /^\s*-\s+(.+)$/.exec(line); var ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
      if (unordered || ordered) { var list = document.createElement(ordered ? "ol" : "ul"); while (index < lines.length) { var itemMatch = (ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*-\s+(.+)$/).exec(lines[index]); if (!itemMatch) break; var item = el("li", "", ""); appendInlineMarkdown(item, itemMatch[1]); list.appendChild(item); index += 1; } container.appendChild(list); continue; }
      var paragraph = el("p", "", ""); appendInlineMarkdown(paragraph, line); index += 1; while (index < lines.length && lines[index].trim() && !/^(#{1,3})\s+/.test(lines[index]) && !/^\s*(?:-|\d+\.|>|---)/.test(lines[index])) { paragraph.appendChild(document.createElement("br")); appendInlineMarkdown(paragraph, lines[index]); index += 1; } container.appendChild(paragraph);
    }
    return container;
  }

  function briefMarkdownBlock(brief) {
    var wrap = el("div", "brief-markdown-block"); var controls = el("div", "markdown-controls"); var previewButton = el("button", "text-button is-selected", "预览"); var sourceButton = el("button", "text-button", "原文"); var preview = renderMarkdown(brief.brief_markdown); var source = el("pre", "markdown-source", valueOrEmpty(brief.brief_markdown, "暂无正文"));
    function select(mode) { var showPreview = mode === "preview"; preview.hidden = !showPreview; source.hidden = showPreview; previewButton.classList.toggle("is-selected", showPreview); sourceButton.classList.toggle("is-selected", !showPreview); }
    previewButton.type = "button"; sourceButton.type = "button"; previewButton.addEventListener("click", function () { select("preview"); }); sourceButton.addEventListener("click", function () { select("source"); }); controls.appendChild(previewButton); controls.appendChild(sourceButton); wrap.appendChild(controls); wrap.appendChild(preview); source.hidden = true; wrap.appendChild(source); return wrap;
  }

  function renderBriefCard(brief, titleText, kicker) {
    var p = panel(titleText || valueOrEmpty(brief.title, "未命名 Brief"), kicker || "已持久化 Brief"); var meta = el("div", "brief-meta");
    [["简报类型", "brief_type"], ["触发类型", "trigger_type"], ["创建时间", "created_at"], ["更新时间", "updated_at"]].forEach(function (item) { var metaItem = el("div", "meta-item"); metaItem.appendChild(el("span", "meta-label", item[0])); metaItem.appendChild(el("span", "meta-value", valueOrEmpty(brief[item[1]], "—"))); meta.appendChild(metaItem); });
    p.appendChild(meta); p.appendChild(el("h3", "subheading", "摘要")); p.appendChild(el("p", "brief-summary", valueOrEmpty(brief.executive_summary, "暂无摘要")));
    p.appendChild(el("h3", "subheading", "候选情报 ID")); p.appendChild(el("p", "candidate-ids", (brief.candidate_ids || []).join(", ") || "无"));
    p.appendChild(el("h3", "subheading", "Brief 正文")); p.appendChild(briefMarkdownBlock(brief)); return p;
  }

  function renderIntelligenceResult(resultNode, result, brief) {
    clear(resultNode); resultNode.className = "write-result is-success";
    resultNode.appendChild(el("strong", "", "科研情报生成成功"));
    resultNode.appendChild(el("div", "report-result-row", "Workflow 状态：" + valueOrEmpty(result.workflow_status, "—")));
    resultNode.appendChild(el("div", "report-result-row", "持久化状态：已写入 ResearchTwin 项目数据"));
    if (brief) resultNode.appendChild(renderBriefCard(brief, "本次生成结果", "本次 Workflow 结果"));
  }

  function renderIntelligence(data) {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "RESEARCH INTELLIGENCE")); intro.appendChild(el("h2", "section-heading", "科研情报")); intro.appendChild(el("p", "section-description", "根据当前项目上下文检索、筛选并沉淀值得关注的外部科研情报。Brief 是 Agent 生成并持久化的项目沟通产物，不等于正式 Project Knowledge。")); root.appendChild(intro);
    var workflow = el("div", "workflow-notice"); workflow.appendChild(el("span", "notice-label", "WORKFLOW 状态"));
    var workflowStatus = data.workflow_status || {}; workflow.appendChild(el("span", "", workflowStatus.available ? "百炼 Research Intelligence Workflow 已配置。" : "百炼 Research Intelligence Workflow 尚未配置。")); root.appendChild(workflow);
    var formPanel = panel("生成科研情报", "百炼 Workflow"); var form = el("form", "write-form");
    addField(form, "科研需求", "query", "textarea", true, "根据当前 ResearchTwin 项目状态，检索近期与 MCP、Agent 工具可靠性、科研智能体相关的高价值研究进展，并生成项目相关科研情报简报。");
    addField(form, "项目", "project_name", "text", true, "ResearchTwin");
    addSelect(form, "简报类型", "brief_type", ["daily", "weekly", "on_demand"], true, "daily", { daily: "每日", weekly: "每周", on_demand: "按需" });
    addField(form, "每个来源结果上限", "limit_per_source", "number", true, "5");
    addField(form, "最多候选情报数", "max_candidates", "number", true, "3");
    var submit = writeButton(form, "生成科研情报"); submit.setAttribute("data-label", "生成科研情报"); var result = el("div", "write-result");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var payload = { query: form.query.value.trim(), project_name: form.project_name.value.trim(), brief_type: form.brief_type.value, limit_per_source: Number(form.limit_per_source.value), max_candidates: Number(form.max_candidates.value) };
      if (!window.confirm("将调用百炼 Workflow，并把生成的 Brief 持久化到 ResearchTwin 项目数据。\n\n提交内容：\n" + JSON.stringify(payload, null, 2))) return;
      submit.disabled = true; submit.textContent = "正在生成…"; showMessage("", "");
      postJson("/api/intelligence/generate", payload).then(async function (response) {
        var refreshed = await getJson("/api/intelligence");
        renderIntelligenceResult(result, response, refreshed.brief);
      }).catch(function (error) { showMessage("is-error", error.message, error.detail); clear(result); }).finally(function () { submit.disabled = false; submit.textContent = submit.getAttribute("data-label") || "生成科研情报"; });
    });
    formPanel.appendChild(form); formPanel.appendChild(result); root.appendChild(formPanel);
    if (data.brief) root.appendChild(renderBriefCard(data.brief, "历史最新科研情报", "历史记录")); else root.appendChild(el("div", "empty-state", "暂无历史科研情报 Brief。当前页面不会编造结果。"));
  }

  function renderCandidates(data) {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "CANDIDATE INTELLIGENCE")); intro.appendChild(el("h2", "section-heading", "候选情报")); intro.appendChild(el("p", "section-description", "Candidate 是待评估的外部情报，不等于正式 Project Knowledge。")); root.appendChild(intro);
    var lifecycle = el("div", "lifecycle"); ["discovered", "shortlisted", "validated", "promoted", "rejected"].forEach(function (s, i) { lifecycle.appendChild(badge(s)); if (i < 3) lifecycle.appendChild(el("span", "lifecycle-arrow", "→")); }); root.appendChild(lifecycle);
    var candidates = data.candidates || []; if (!candidates.length) { root.appendChild(el("div", "empty-state", "暂无 Candidate。")); return; }
    var table = el("div", "candidate-list"); candidates.forEach(function (candidate) { var item = el("article", "candidate-card"); var head = el("div", "candidate-head"); head.appendChild(el("h3", "candidate-title", valueOrEmpty(candidate.title, "未命名候选情报"))); head.appendChild(badge(candidate.status)); item.appendChild(head); item.appendChild(el("div", "candidate-source", valueOrEmpty(candidate.source_type, "未知") + " · " + valueOrEmpty(candidate.source_url, "无来源链接"))); item.appendChild(el("p", "candidate-summary", valueOrEmpty(candidate.summary, "暂无摘要"))); item.appendChild(el("p", "candidate-relevance", "相关性：" + valueOrEmpty(candidate.relevance_reason, "暂无说明"))); var footer = el("div", "candidate-footer"); footer.appendChild(el("span", "", "置信度：" + valueOrEmpty(candidate.confidence, "—"))); footer.appendChild(el("span", "", valueOrEmpty(candidate.updated_at || candidate.created_at, "—"))); item.appendChild(footer); table.appendChild(item); }); root.appendChild(table);
  }

  function renderKnowledge(data) {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "PROJECT KNOWLEDGE")); intro.appendChild(el("h2", "section-heading", "项目知识")); intro.appendChild(el("p", "section-description", "Candidate 不等于正式 Project Knowledge。这里仅展示 Remote MCP 返回的正式知识记录。")); root.appendChild(intro);
    var records = data.knowledge || []; if (!records.length) { root.appendChild(el("div", "empty-state", "暂无正式 Project Knowledge。外部 Candidate 不会自动进入知识库。")); return; }
    var list = el("div", "knowledge-list"); records.forEach(function (record) { var item = el("article", "knowledge-card"); var head = el("div", "candidate-head"); head.appendChild(el("h3", "candidate-title", valueOrEmpty(record.title, "未命名知识"))); head.appendChild(badge(record.sync_status)); item.appendChild(head); item.appendChild(el("p", "candidate-source", valueOrEmpty(record.knowledge_type, "未知") + " · Candidate " + valueOrEmpty(record.candidate_id, "—"))); item.appendChild(el("p", "candidate-summary", valueOrEmpty(record.knowledge_content, "暂无内容"))); list.appendChild(item); }); root.appendChild(list);
  }

  function renderAdvisor(data) {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "ADVISOR")); intro.appendChild(el("h2", "section-heading", "导师要求")); intro.appendChild(el("p", "section-description", "导师要求会被持久化为 ResearchTwin 长期项目上下文的一部分。")); root.appendChild(intro);
    var formPanel = panel("记录导师要求", "真实 MCP 写入"); var form = el("form", "write-form"); addField(form, "导师要求", "instruction", "textarea", true); addField(form, "任务", "task", "text", true); addSelect(form, "优先级", "priority", ["low", "medium", "high", "critical"], true, "medium"); addField(form, "截止日期", "deadline", "date", false); addField(form, "约束条件", "constraints", "textarea", false, "仅使用匿名 Demo 数据"); addField(form, "后续跟进", "follow_up", "textarea", false); addField(form, "来源说明", "source_note", "text", false, "Demo 验收");
    var submit = writeButton(form, "保存导师要求"); submit.setAttribute("data-label", "保存导师要求"); var result = el("div", "write-result");
    form.addEventListener("submit", function (event) { event.preventDefault(); var payload = { instruction: form.instruction.value.trim(), task: form.task.value.trim(), priority: form.priority.value }; if (form.deadline.value) payload.deadline = form.deadline.value; if (form.constraints.value.trim()) payload.constraints = lines(form.constraints.value); if (form.follow_up.value.trim()) payload.follow_up = form.follow_up.value.trim(); if (form.source_note.value.trim()) payload.source_note = form.source_note.value.trim(); submitWrite(form, submit, "/api/advisor-instructions", payload, "这条导师要求将被持久化到 ResearchTwin 项目记忆中。", result, async function () { await refreshOverviewData(); renderAdvisor(state.overview); }); });
    formPanel.appendChild(form); formPanel.appendChild(result); root.appendChild(formPanel);
    var history = panel("最近导师要求", "REMOTE MCP 上下文"); var records = (data.recent_advisor_instructions || []).slice().sort(function (a, b) { return String(b.created_at || "").localeCompare(String(a.created_at || "")); });
    if (!records.length) history.appendChild(el("p", "empty-inline", "暂无导师要求。"));
    records.forEach(function (record) { var item = el("div", "history-row"); var head = el("div", "candidate-head"); head.appendChild(el("strong", "", valueOrEmpty(record.task, "未指定任务"))); head.appendChild(badge(record.priority)); item.appendChild(head); item.appendChild(el("p", "candidate-summary", valueOrEmpty(record.instruction, "暂无内容"))); item.appendChild(el("div", "advisor-detail", "截止日期：" + valueOrEmpty(record.deadline, "未设置"))); if (record.constraints && record.constraints.length) item.appendChild(el("div", "advisor-detail", "约束条件：" + (Array.isArray(record.constraints) ? record.constraints.join(" · ") : record.constraints))); if (record.follow_up) item.appendChild(el("div", "advisor-detail", "后续跟进：" + record.follow_up)); if (record.source_note) item.appendChild(el("div", "advisor-detail", "来源说明：" + record.source_note)); if (record.created_at) item.appendChild(el("div", "candidate-footer", "创建时间：" + record.created_at)); history.appendChild(item); }); root.appendChild(history);
  }

  function renderActivities(data) {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "ACTIVITIES")); intro.appendChild(el("h2", "section-heading", "科研活动")); intro.appendChild(el("p", "section-description", "提交后会通过 Remote MCP 持久化为科研活动记录。")); root.appendChild(intro);
    var formPanel = panel("记录科研活动", "真实 MCP 写入"); var form = el("form", "write-form"); addSelect(form, "活动类型", "activity_type", ["analysis", "coding", "data_collection", "debugging", "experiment", "meeting", "other", "paper_reading", "writing"], true, "coding"); addField(form, "标题", "title", "text", true, "【Demo 验收】完成 ResearchTwin 本地 Dashboard 与 Remote MCP/FC/NAS 真实链路验收。"); addField(form, "描述", "description", "textarea", true); addField(form, "日期", "date", "date", false); addField(form, "结果", "result", "textarea", false); addField(form, "阻塞问题", "problem", "textarea", false); addField(form, "下一步", "next_step", "textarea", false); addField(form, "标签", "tags", "textarea", false, "Demo 验收\nResearchTwin"); addField(form, "来源", "source", "text", false, "Demo 验收");
    var submit = writeButton(form, "保存科研活动"); submit.setAttribute("data-label", "保存科研活动"); var result = el("div", "write-result"); form.addEventListener("submit", function (event) { event.preventDefault(); var payload = { activity_type: form.activity_type.value, title: form.title.value.trim(), description: form.description.value.trim() }; ["date", "result", "problem", "next_step", "source"].forEach(function (key) { if (form[key].value.trim()) payload[key] = form[key].value.trim(); }); if (form.tags.value.trim()) payload.tags = lines(form.tags.value); submitWrite(form, submit, "/api/activities", payload, "这条科研活动将被持久化。", result, async function () { await refreshOverviewData(); renderActivities(state.overview); }); }); formPanel.appendChild(form); formPanel.appendChild(result); root.appendChild(formPanel);
    var timeline = panel("最近科研活动", "REMOTE MCP 上下文"); var activities = data.recent_activities || []; if (!activities.length) timeline.appendChild(el("p", "empty-inline", "暂无活动记录。")); activities.forEach(function (activity) { var item = el("div", "history-row timeline-row"); item.appendChild(el("div", "timeline-date", valueOrEmpty(activity.date, "—"))); var body = el("div", "timeline-body"); body.appendChild(el("strong", "timeline-title", valueOrEmpty(activity.title, "未命名活动"))); body.appendChild(el("p", "candidate-summary", valueOrEmpty(activity.description, "暂无描述"))); if (activity.result) body.appendChild(el("p", "activity-detail", "结果：" + activity.result)); if (activity.problem) body.appendChild(el("p", "activity-detail is-problem", "阻塞问题：" + activity.problem)); if (activity.next_step) body.appendChild(el("p", "activity-detail", "下一步：" + activity.next_step)); body.appendChild(el("p", "activity-tags", (activity.tags || []).join(" · ") || "无标签")); item.appendChild(body); timeline.appendChild(item); }); root.appendChild(timeline);
  }

  function renderReportResult(resultNode, result) {
    clear(resultNode); resultNode.className = "write-result is-success"; resultNode.appendChild(el("strong", "", "报告生成成功"));
    var fields = [["状态", "status"], ["报告类型", "report_type"], ["报告路径", "report_path"], ["生成时间", "generated_at"]]; var meta = el("div", "report-result-meta");
    fields.forEach(function (field) { if (result[field[1]] !== undefined && result[field[1]] !== null) { var row = el("div", "report-result-row"); row.appendChild(el("span", "meta-label", field[0])); row.appendChild(el("span", "meta-value", result[field[1]])); meta.appendChild(row); } }); resultNode.appendChild(meta);
    if (result.report !== undefined && result.report !== null) { resultNode.appendChild(el("h3", "subheading", "报告正文")); resultNode.appendChild(el("pre", "markdown-preview", result.report)); }
  }

  function renderReports() {
    clear(root); var intro = el("section", "page-intro"); intro.appendChild(el("p", "eyebrow", "REPORTS")); intro.appendChild(el("h2", "section-heading", "阶段报告")); intro.appendChild(el("p", "section-description", "报告根据已持久化的 ResearchTwin 项目记录生成。")); root.appendChild(intro);
    var formPanel = panel("生成阶段报告", "真实 MCP 写入"); var form = el("form", "write-form"); addField(form, "项目", "project_name", "text", true, "ResearchTwin"); addSelect(form, "报告类型", "report_type", ["weekly", "meeting", "stage"], true, "stage", { weekly: "每周", meeting: "会议", stage: "阶段" }); addField(form, "开始日期", "start_date", "date", true); addField(form, "结束日期", "end_date", "date", true);
    var submit = writeButton(form, "生成阶段报告"); submit.setAttribute("data-label", "生成阶段报告"); var result = el("div", "write-result"); form.addEventListener("submit", function (event) { event.preventDefault(); var payload = { project_name: form.project_name.value.trim(), report_type: form.report_type.value, start_date: form.start_date.value, end_date: form.end_date.value }; if (!window.confirm("这将根据已持久化的 ResearchTwin 记录生成报告。\n\n提交内容：\n" + JSON.stringify(payload, null, 2))) return; submit.disabled = true; submit.textContent = "正在生成…"; showMessage("", ""); postJson("/api/reports", payload).then(function (response) { renderReportResult(result, response); }).catch(function (error) { showMessage("is-error", error.message, error.detail); clear(result); }).finally(function () { submit.disabled = false; submit.textContent = submit.getAttribute("data-label") || "生成阶段报告"; }); }); formPanel.appendChild(form); formPanel.appendChild(result); root.appendChild(formPanel);
  }

  function render(view, data) {
    state.view = view; title.textContent = { overview: "项目总览", intelligence: "科研情报", candidates: "候选情报", advisor: "导师要求", activities: "科研活动", reports: "阶段报告", knowledge: "项目知识" }[view]; document.querySelectorAll(".nav-item").forEach(function (item) { item.classList.toggle("is-active", item.getAttribute("data-view") === view); });
    if (view === "overview") renderOverview(data); else if (view === "intelligence") renderIntelligence(data); else if (view === "candidates") renderCandidates(data); else if (view === "advisor") renderAdvisor(data); else if (view === "activities") renderActivities(data); else if (view === "reports") renderReports(); else renderKnowledge(data);
  }

  async function loadOverview(showLoading) {
    if (showLoading) setLoading("正在读取 Remote MCP 项目上下文…"); showMessage("", "");
    try { var result = await Promise.all([getJson("/api/health"), getJson("/api/overview")]); state.health = result[0]; state.overview = result[1]; updateConnection(state.health); render("overview", state.overview); refreshTime.textContent = "刷新于 " + new Date().toLocaleTimeString(); }
    catch (error) { updateConnection(state.health); showMessage("is-error", error.message, error.detail); clear(root); root.appendChild(el("div", "error-state", "当前无法加载项目数据，请检查本地 token、网络和 Remote MCP 状态。")); }
  }

  async function loadView(view) {
    if (view === "overview") { await loadOverview(true); return; }
    if (view === "reports") { showMessage("", ""); render("reports"); return; }
    setLoading("正在读取 Remote MCP 数据…"); showMessage("", "");
    try {
      var paths = { candidates: "/api/candidates", knowledge: "/api/knowledge", advisor: "/api/overview", activities: "/api/overview" };
      var data;
      if (view === "intelligence") { var results = await Promise.all([getJson("/api/intelligence"), getJson("/api/workflow-status")]); data = results[0]; data.workflow_status = results[1]; } else data = await getJson(paths[view]);
      render(view, data); refreshTime.textContent = "刷新于 " + new Date().toLocaleTimeString();
    } catch (error) { showMessage("is-error", error.message, error.detail); clear(root); root.appendChild(el("div", "error-state", "当前无法加载这个视图，请检查本地 token、网络和 Remote MCP 状态。")); }
  }

  function navigate(view) { loadView(view); }
  document.querySelectorAll(".nav-item").forEach(function (item) { item.addEventListener("click", function () { navigate(item.getAttribute("data-view")); }); });
  document.getElementById("refresh-button").addEventListener("click", function () { loadView(state.view); });
  loadOverview(true);
}());
