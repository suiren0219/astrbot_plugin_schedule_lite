(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const PLUGIN_NAME = "astrbot_plugin_schedule_lite";

  function getBridge() {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    if (window.AstrBotPluginView) return window.AstrBotPluginView;
    try {
      // iframe 场景：bridge 可能注入在父窗口，且注入时间晚于本脚本
      if (window.parent && window.parent !== window) {
        if (window.parent.AstrBotPluginPage) return window.parent.AstrBotPluginPage;
        if (window.parent.AstrBotPluginView) return window.parent.AstrBotPluginView;
      }
    } catch (err) {
      return null;
    }
    return null;
  }

  function usableBridge(bridge) {
    return Boolean(
      bridge
      && typeof bridge.apiGet === "function"
      && typeof bridge.apiPost === "function"
    );
  }

  async function waitForBridge(timeoutMs = 8000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const bridge = getBridge();
      if (usableBridge(bridge)) return bridge;
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    return getBridge();
  }

  let cachedBridge = null;
  let bridgeProbed = false;
  async function ensureBridge() {
    if (usableBridge(cachedBridge)) return cachedBridge;
    let bridge = getBridge();
    if (usableBridge(bridge)) {
      cachedBridge = bridge;
      bridgeProbed = true;
      return cachedBridge;
    }
    if (bridgeProbed) return null;
    bridgeProbed = true;
    bridge = await waitForBridge();
    if (usableBridge(bridge)) cachedBridge = bridge;
    return cachedBridge;
  }
  const THEME_KEY = "schedule-lite-theme";
  const THEME_ICON = { auto: "◐", light: "☀", dark: "☾" };
  const themeQuery = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;
  const state = {
    view: "overview",
    records: [],
    editingKey: "",
    courses: [],
    adjustments: [],
    settings: {},
    holidayYear: new Date().getFullYear(),
    themeMode: "auto",
    bridgeDark: null,
    restorePayload: null,
  };

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  function toast(message, ms = 2400) {
    const el = $("#toast");
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, ms);
  }

  /* --------------------------------------------------------------- theme */

  function readThemeMode() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === "auto" || saved === "light" || saved === "dark") return saved;
    } catch (err) {
      /* 在受限 iframe 里 localStorage 可能不可用 */
    }
    return "auto";
  }

  function contextDark(ctx) {
    if (!ctx || typeof ctx !== "object") return null;
    if (typeof ctx.isDark === "boolean") return ctx.isDark;
    if (typeof ctx.dark === "boolean") return ctx.dark;
    if (ctx.theme === "dark") return true;
    if (ctx.theme === "light") return false;
    return null;
  }

  function applyTheme(mode, systemDark) {
    if (mode !== "auto" && mode !== "light" && mode !== "dark") mode = "auto";
    if (typeof systemDark === "boolean") state.bridgeDark = systemDark;
    state.themeMode = mode;

    let resolved = mode;
    if (mode === "auto") {
      const dark = typeof state.bridgeDark === "boolean"
        ? state.bridgeDark
        : Boolean(themeQuery && themeQuery.matches);
      resolved = dark ? "dark" : "light";
    }
    document.documentElement.dataset.theme = resolved;
    try { localStorage.setItem(THEME_KEY, mode); } catch (err) { /* 忽略 */ }

    const button = $("#theme-toggle");
    if (button) {
      const label = mode === "auto" ? "跟随系统" : mode === "light" ? "浅色" : "深色";
      button.textContent = THEME_ICON[mode];
      button.title = `主题：${label}（点击切换）`;
      button.setAttribute("aria-label", button.title);
    }
  }

  function cycleTheme() {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(state.themeMode) + 1) % order.length];
    applyTheme(next);
  }

  let confirmResolver = null;

  function askConfirm(message, title = "确认操作", okText = "确定") {
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = message;
    $("#confirm-ok").textContent = okText;
    $("#confirm-mask").hidden = false;
    return new Promise((resolve) => {
      confirmResolver = resolve;
    });
  }

  function resolveConfirm(value) {
    $("#confirm-mask").hidden = true;
    if (confirmResolver) {
      confirmResolver(value);
      confirmResolver = null;
    }
  }

  async function ready() {
    const bridge = await ensureBridge();
    if (bridge) {
      try {
        const ctx = typeof bridge.ready === "function" ? await bridge.ready() : null;
        applyTheme(readThemeMode(), contextDark(ctx));
        if (typeof bridge.onContext === "function") {
          bridge.onContext((next) => {
            const dark = contextDark(next);
            if (typeof dark === "boolean") applyTheme(state.themeMode, dark);
          });
        }
        $("#conn-text").textContent = (ctx && ctx.displayName) || "已连接";
        $("#conn-dot").classList.remove("off");
        return;
      } catch (err) {
        console.error(err);
      }
    }
    applyTheme(readThemeMode());
    $("#conn-text").textContent = "未连接后台 · 请从 AstrBot 插件页打开";
    $("#conn-text").title = "点击重试连接";
    $("#conn-dot").classList.add("off");
  }

  function unwrapResponse(payload) {
    if (payload && typeof payload === "object") {
      if (payload.status === "ok" && Object.prototype.hasOwnProperty.call(payload, "data")) {
        return payload.data;
      }
      if (payload.success === true && Object.prototype.hasOwnProperty.call(payload, "data")) {
        return payload.data;
      }
      if (["error", "fail", "failed"].includes(String(payload.status || "").toLowerCase())) {
        throw new Error(payload.message || payload.error || "请求失败");
      }
      if (payload.error) throw new Error(payload.error);
    }
    return payload === undefined || payload === null ? {} : payload;
  }

  function isRouteMissing(value) {
    const text = String(
      (value && (value.message || value.error || value.detail)) || value || ""
    ).toLowerCase();
    return /未找到.*路由|路由不存在|route.*not.*found|not.*found.*route|http\s*404|\b404\b/.test(text);
  }

  function endpointCandidates(path) {
    const clean = String(path || "").replace(/^\/+/, "");
    return [...new Set([
      `page/${clean}`,
      clean,
      `/${clean}`,
      `${PLUGIN_NAME}/page/${clean}`,
    ])];
  }

  let endpointStyle = "";
  async function bridgeRequest(method, path, payload) {
    const bridge = await ensureBridge();
    if (!bridge) throw new Error("no-bridge");
    const candidates = endpointCandidates(path);
    if (endpointStyle) {
      const preferred = candidates.find((item) => item === endpointStyle);
      if (preferred) {
        candidates.splice(candidates.indexOf(preferred), 1);
        candidates.unshift(preferred);
      }
    }
    const errors = [];
    for (const endpoint of candidates) {
      try {
        const raw = method === "GET"
          ? await bridge.apiGet(endpoint, payload)
          : await bridge.apiPost(endpoint, payload || {});
        if (isRouteMissing(raw)) {
          errors.push(String((raw && (raw.message || raw.error)) || "未找到页面 API 路由"));
          continue;
        }
        endpointStyle = endpoint;
        return unwrapResponse(raw);
      } catch (err) {
        if (isRouteMissing(err)) {
          errors.push(String((err && err.message) || err));
          continue;
        }
        throw err;
      }
    }
    throw new Error(errors[0] || "未找到页面 API 路由，请确认插件已启用并重载");
  }

  function markHttpConnected() {
    if (!usableBridge(cachedBridge)) {
      $("#conn-text").textContent = "已连接（HTTP）";
      $("#conn-dot").classList.remove("off");
    }
  }

  async function httpRequest(method, path, payload) {
    if (window.location.protocol === "file:") throw new Error("file:// 无法直连后台");
    const clean = String(path || "").replace(/^\/+/, "");
    const url = new URL(`/api/plug/${PLUGIN_NAME}/page/${clean}`, window.location.origin);
    const options = {
      method,
      credentials: "same-origin",
      headers: {},
    };
    if (method === "GET") {
      Object.entries(payload || {}).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, value);
        }
      });
    } else {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload || {});
    }
    const response = await fetch(url.toString(), options);
    const text = await response.text();
    let raw = {};
    if (text) {
      try {
        raw = JSON.parse(text);
      } catch (err) {
        throw new Error(response.ok ? "后台返回的不是 JSON" : `HTTP ${response.status}`);
      }
    }
    if (!response.ok) {
      if (raw && (raw.message || raw.error)) throw new Error(raw.message || raw.error);
      throw new Error(`HTTP ${response.status}`);
    }
    return unwrapResponse(raw);
  }

  async function apiGet(path, params = {}) {
    if (usableBridge(await ensureBridge())) return bridgeRequest("GET", path, params);
    try {
      const data = await httpRequest("GET", path, params);
      markHttpConnected();
      return data;
    } catch (err) {
      console.warn("[schedule-lite] HTTP 直连失败，使用预览数据", err);
      return demo(path, params);
    }
  }
  async function apiPost(path, body = {}) {
    if (usableBridge(await ensureBridge())) return bridgeRequest("POST", path, body);
    try {
      const data = await httpRequest("POST", path, body);
      markHttpConnected();
      return data;
    } catch (err) {
      console.warn("[schedule-lite] HTTP 直连保存失败", err);
      return demo(path, body);
    }
  }

  /* ---------------------------------------------------------------- demo */

  function demo(path, body) {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const weekday = `周${"日一二三四五六"[now.getDay()]}`;
    if (path === "overview") {
      return {
        users: 0, courses: 0, groups: 0, today, weekday,
        status_counts: {},
        holiday: { name: "", off: false, workday: false, source: "" },
        config: { store_teacher: false, store_location: false, everyone_image: true, help_image: true },
      };
    }
    if (path === "live") return { items: [], total: 0 };
    if (path === "timetables") return { items: [], total: 0 };
    if (path === "timetable") return { ...blankRecord(), key: (body && body.key) || "" };
    if (path === "settings") {
      return {
        store_teacher: false, store_location: false, everyone_image: true, avatar_enabled: true, help_image: true,
        everyone_page_size: 8, everyone_hide_idle_over: 12,
        showcase_enabled: false, showcase_time: "22:30", showcase_min_minutes: 300, showcase_min_users: 2,
        recap_enabled: false, recap_weekday: 5, recap_time: "21:00", recap_mode: "week", recap_top: 5,
        holiday_enabled: true, holiday_api_url: "https://timor.tech/api/holiday/year/{year}",
        holiday_cache_hours: 24, makeup_weekday_map: "",
        early_enabled: false, early_time: "21:30", early_max_start: "08:00", early_at: false,
      };
    }
    if (path === "holidays") {
      return { year: now.getFullYear(), source: "", updated_at: "", days: [] };
    }
    if (path === "timetable/save") throw new Error("未连接 AstrBot 后台，保存不会生效；请从插件控制台打开");
    if (path === "timetable/delete") throw new Error("未连接 AstrBot 后台，删除不会生效；请从插件控制台打开");
    if (path === "import") throw new Error("预览模式不支持导入，请连接 AstrBot 后台");
    if (path === "backup") throw new Error("预览模式不支持备份，请连接 AstrBot 后台");
    if (path === "restore") throw new Error("预览模式不支持恢复，请连接 AstrBot 后台");
    if (path === "events") return { items: [] };
    if (path === "events/save") throw new Error("未连接 AstrBot 后台，重要日期不会保存");
    if (path === "settings/save") throw new Error("未连接 AstrBot 后台，设置不会保存");
    if (path === "holidays/refresh") throw new Error("未连接 AstrBot 后台，无法刷新节假日");
    return {};
  }

  /* ---------------------------------------------------------------- tabs */

  function switchView(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach((el) => {
      el.hidden = el.id !== `view-${view}`;
    });
    document.querySelectorAll(".tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.view === view);
    });
    if (view === "overview") loadOverview();
    if (view === "data") loadRecords();
    if (view === "holiday") loadHoliday();
    if (view === "settings") loadSettings();
  }

  /* ---------------------------------------------------------------- overview */

  async function loadOverview() {
    try {
      const data = await apiGet("overview");
      $("#stat-users").textContent = data.users ?? "—";
      $("#stat-courses").textContent = data.courses ?? "—";
      $("#stat-groups").textContent = data.groups ?? "—";
      $("#stat-today").textContent = data.today || "—";
      const hol = data.holiday || {};
      $("#stat-holiday").textContent = hol.name
        ? `${hol.name}${hol.off ? " · 放假" : " · 补班"}`
        : (data.weekday || "");
      const chips = Object.entries(data.status_counts || {})
        .filter(([, n]) => n > 0)
        .map(([label, n]) => `<span class="chip">${esc(label)}<b>${n}</b></span>`);
      $("#status-chips").innerHTML = chips.length ? chips.join("") : '<span class="muted">今天还没有状态数据</span>';
      await loadLive();
    } catch (err) {
      toast(`概览加载失败：${err.message}`);
    }
  }

  function avatarTag(name, url) {
    const initial = esc((name || "?").slice(0, 1));
    if (!url) return `<span class="avatar">${initial}</span>`;
    return `<span class="avatar"><b>${initial}</b><img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></span>`;
  }

  async function loadLive() {
    try {
      const data = await apiGet("live");
      const items = data.items || [];
      $("#live-list").innerHTML = items.length
        ? items.map((item) => `
          <div class="live-row">
            ${avatarTag(item.name, item.avatar)}
            <div class="live-main"><b>${esc(item.name)}</b><small>${esc(item.main || "")}${item.sub ? " · " + esc(item.sub) : ""}</small></div>
            <span class="pill t${item.type}">${esc(item.label)}</span>
          </div>`).join("")
        : '<div class="empty">本群还没有人导入课表</div>';
    } catch (err) {
      $("#live-list").innerHTML = `<div class="empty">状态读取失败：${esc(err.message)}</div>`;
    }
  }

  /* ---------------------------------------------------------------- records */

  async function loadRecords() {
    const q = $("#search").value.trim();
    try {
      const data = await apiGet("timetables", q ? { q } : {});
      state.records = data.items || [];
      const body = $("#record-body");
      body.innerHTML = state.records.length
        ? state.records.map((r) => `
          <tr>
            <td><b>${esc(r.owner_name || r.owner_id)}</b><span class="sub">${esc(r.key)}</span></td>
            <td>${esc(r.name)}<span class="sub">${esc(r.source || "")}</span></td>
            <td>${esc(r.term_start)}<span class="sub">共 ${r.total_weeks} 周</span></td>
            <td>${r.course_count}<span class="sub">请假 ${r.leave_count || 0} · 调课 ${r.adjustment_count || 0}</span></td>
            <td>${(r.groups || []).length}</td>
            <td><div class="tiny-actions">
              <button class="tiny" data-edit="${esc(r.key)}">编辑</button>
              <button class="tiny danger" data-del="${esc(r.key)}">删除</button>
            </div></td>
          </tr>`).join("")
        : '<tr><td colspan="6" class="empty">没有匹配的课表</td></tr>';
      body.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => openEditor(btn.dataset.edit));
      });
      body.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", () => deleteRecord(btn.dataset.del));
      });
    } catch (err) {
      toast(`列表加载失败：${err.message}`);
    }
  }

  function mondayOf(dateStr) {
    const d = dateStr ? new Date(dateStr + "T00:00:00") : new Date();
    const day = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - day);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function blankRecord() {
    return {
      key: "", owner_id: "", owner_name: "", name: "我的课表",
      term_start: mondayOf(), total_weeks: 20, timezone: "Asia/Shanghai",
      source: "控制面板", courses: [],
    };
  }

  function courseRow(course = {}) {
    const weeks = Array.isArray(course.weeks) ? course.weeks.join(",") : (course.weeks || "1-16");
    return `
      <div class="course-row">
        <label>课程<input class="input" data-field="name" value="${esc(course.name || "")}" placeholder="高等数学"></label>
        <label>星期<select class="input" data-field="day">${[1,2,3,4,5,6,7].map((d) => `<option value="${d}" ${Number(course.day) === d ? "selected" : ""}>周${"一二三四五六日"[d-1]}</option>`).join("")}</select></label>
        <label>周次<input class="input" data-field="weeks" value="${esc(weeks)}" placeholder="1-16 或 1-8,10-16"></label>
        <label>开始<input class="input" data-field="start" value="${esc(course.start || "")}" placeholder="08:30"></label>
        <label>结束<input class="input" data-field="end" value="${esc(course.end || "")}" placeholder="10:05"></label>
        <label>起始节<input class="input" data-field="start_section" type="number" value="${esc(course.start_section || "")}" placeholder="1"></label>
        <label>结束节<input class="input" data-field="end_section" type="number" value="${esc(course.end_section || "")}" placeholder="2"></label>
        <label>地点<input class="input" data-field="location" value="${esc(course.location || "")}" placeholder="A101"></label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function renderCourses() {
    $("#course-editor").innerHTML = state.courses.length
      ? state.courses.map(courseRow).join("")
      : '<div class="empty">还没有课程，点「添加一门课」</div>';
    document.querySelectorAll("#course-editor .remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        btn.closest(".course-row").remove();
      });
    });
  }

  function leaveRow(item = {}) {
    return `
      <div class="record-row leave-row">
        <label>开始<input class="input" type="date" data-field="start" value="${esc(item.start || "")}"></label>
        <label>结束<input class="input" type="date" data-field="end" value="${esc(item.end || item.start || "")}"></label>
        <label class="grow">原因<input class="input" data-field="reason" value="${esc(item.reason || "")}" placeholder="病假（可空）"></label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function existingAdjustmentRow(item, index) {
    const names = (item.courses || []).map((course) => course.name).filter(Boolean).join("、");
    return `
      <div class="record-row adjust-row">
        <div class="record-main">
          <b>${esc(item.from_date)} → ${esc(item.to_date)}</b>
          <small>${esc(item.course || "全部课程")}${names ? " · " + esc(names) : ""}</small>
        </div>
        <button class="icon-btn remove" data-remove="${index}" title="移除">✕</button>
      </div>`;
  }

  function newAdjustmentRow(item = {}) {
    return `
      <div class="record-row adjust-row new">
        <label>原日期<input class="input" type="date" data-field="from_date" value="${esc(item.from_date || "")}"></label>
        <label>目标日期<input class="input" type="date" data-field="to_date" value="${esc(item.to_date || "")}"></label>
        <label>课程<input class="input" data-field="course" value="${esc(item.course || "")}" placeholder="留空=当天全部"></label>
        <label class="inline-check"><input type="checkbox" data-field="swap" ${item.swap ? "checked" : ""}>互换</label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function makeupRow(day = "", weekday = 1) {
    const options = [1, 2, 3, 4, 5, 6, 7]
      .map((value) => `<option value="${value}" ${Number(weekday) === value ? "selected" : ""}>周${"一二三四五六日"[value - 1]}</option>`)
      .join("");
    return `
      <div class="record-row makeup-row">
        <label>补班日期<input class="input" type="date" data-field="date" value="${esc(day)}"></label>
        <label>按周几<select class="input" data-field="weekday">${options}</select></label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function groupRow(group = "", origin = "") {
    return `
      <div class="record-row group-row">
        <label>群号<input class="input" data-field="group" value="${esc(group)}" placeholder="123456"></label>
        <label class="grow">会话 origin<input class="input" data-field="origin" value="${esc(origin)}" placeholder="aiocqhttp:GroupMessage:123456"></label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function bindRecordRemoval(selector) {
    document.querySelectorAll(`${selector} .remove`).forEach((btn) => {
      btn.addEventListener("click", () => {
        btn.closest(".record-row").remove();
      });
    });
  }

  function renderLeaves(items = []) {
    const wrap = $("#leave-editor");
    wrap.innerHTML = items.length ? items.map(leaveRow).join("") : "";
    bindRecordRemoval("#leave-editor");
  }

  function renderAdjustments(items = []) {
    const wrap = $("#adjust-existing");
    wrap.innerHTML = items.length ? items.map(existingAdjustmentRow).join("") : "";
    wrap.querySelectorAll("[data-remove]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.adjustments.splice(Number(btn.dataset.remove), 1);
        renderAdjustments(state.adjustments);
      });
    });
  }

  function renderMakeup(days = {}) {
    const entries = Object.entries(days).sort(([a], [b]) => a.localeCompare(b));
    const wrap = $("#makeup-editor");
    wrap.innerHTML = entries.length
      ? entries.map(([day, weekday]) => makeupRow(day, weekday)).join("")
      : "";
    bindRecordRemoval("#makeup-editor");
  }

  function renderGroups(groups = [], origins = {}) {
    const wrap = $("#group-editor");
    wrap.innerHTML = groups.length
      ? groups.map((group) => groupRow(group, origins[group] || "")).join("")
      : "";
    bindRecordRemoval("#group-editor");
  }

  function readLeaves() {
    return [...document.querySelectorAll("#leave-editor .leave-row")]
      .map((row) => {
        const value = (field) => row.querySelector(`[data-field="${field}"]`)?.value.trim() || "";
        return { start: value("start"), end: value("end") || value("start"), reason: value("reason") };
      })
      .filter((item) => item.start);
  }

  function readNewAdjustments() {
    const rows = [];
    for (const row of document.querySelectorAll("#adjust-new .adjust-row")) {
      const value = (field) => row.querySelector(`[data-field="${field}"]`)?.value.trim() || "";
      const from = value("from_date");
      const to = value("to_date");
      if (!from && !to) continue;
      if (!from || !to) throw new Error("新增调课需要同时填写原日期和目标日期");
      rows.push({
        from_date: from,
        to_date: to,
        course: value("course"),
        swap: Boolean(row.querySelector('[data-field="swap"]')?.checked),
      });
    }
    return rows;
  }

  function readMakeupDays() {
    const days = {};
    document.querySelectorAll("#makeup-editor .makeup-row").forEach((row) => {
      const day = row.querySelector('[data-field="date"]')?.value.trim();
      if (!day) return;
      days[day] = Number(row.querySelector('[data-field="weekday"]')?.value || 1);
    });
    return days;
  }

  function readGroups() {
    const groups = [];
    const origins = {};
    document.querySelectorAll("#group-editor .group-row").forEach((row) => {
      const group = row.querySelector('[data-field="group"]')?.value.trim();
      if (!group) return;
      if (!groups.includes(group)) groups.push(group);
      const origin = row.querySelector('[data-field="origin"]')?.value.trim();
      if (origin) origins[group] = origin;
    });
    return { groups, origins };
  }

  async function openEditor(key = "") {
    try {
      const data = key ? await apiGet("timetable", { key }) : blankRecord();
      state.editingKey = key;
      $("#editor-title").textContent = key ? "编辑课表" : "新增课表";
      $("#editor-key").textContent =
        (key || "保存后生成 platform:owner_id")
        + (data.raw_source ? ` · 源文件：${data.raw_source}` : "");
      $("#f-platform").value = key ? key.split(":")[0] : "aiocqhttp";
      $("#f-owner-id").value = data.owner_id || "";
      $("#f-owner-name").value = data.owner_name || "";
      $("#f-name").value = data.name || "我的课表";
      $("#f-term").value = data.term_start || mondayOf();
      $("#f-weeks").value = data.total_weeks || 20;
      $("#f-tz").value = data.timezone || "Asia/Shanghai";
      state.courses = Array.isArray(data.courses) ? data.courses.slice() : [];
      state.adjustments = Array.isArray(data.adjustments) ? data.adjustments.slice() : [];
      renderCourses();
      renderLeaves(Array.isArray(data.leaves) ? data.leaves : []);
      renderAdjustments(state.adjustments);
      $("#adjust-new").innerHTML = "";
      renderMakeup((data.makeup_days && typeof data.makeup_days === "object") ? data.makeup_days : {});
      renderGroups(
        Array.isArray(data.groups) ? data.groups : [],
        (data.group_origins && typeof data.group_origins === "object") ? data.group_origins : {},
      );
      $("#delete-record").style.visibility = key ? "visible" : "hidden";
      $("#editor-mask").hidden = false;
    } catch (err) {
      toast(`读取课表失败：${err.message}`);
    }
  }

  function closeEditor() {
    $("#editor-mask").hidden = true;
    state.editingKey = "";
  }

  function readCourses() {
    const rows = [];
    document.querySelectorAll("#course-editor .course-row").forEach((row) => {
      const value = (field) => row.querySelector(`[data-field="${field}"]`)?.value.trim() || "";
      const name = value("name");
      if (!name) return;
      const item = {
        name,
        day: Number(value("day") || 1),
        weeks: value("weeks"),
        start: value("start"),
        end: value("end"),
        start_section: value("start_section") ? Number(value("start_section")) : null,
        end_section: value("end_section") ? Number(value("end_section")) : null,
        location: value("location"),
      };
      rows.push(item);
    });
    return rows;
  }

  async function saveRecord() {
    const courses = readCourses();
    const incomplete = courses.find(
      (item) => (!item.start && !item.start_section) || (item.start && !item.end)
    );
    if (incomplete) {
      return toast(`「${incomplete.name}」缺少上课时间或节次`);
    }
    let newAdjustments;
    try {
      newAdjustments = readNewAdjustments();
    } catch (err) {
      return toast(err.message);
    }
    const groupBinding = readGroups();
    const payload = {
      key: state.editingKey,
      platform: $("#f-platform").value.trim() || "aiocqhttp",
      owner_id: $("#f-owner-id").value.trim(),
      owner_name: $("#f-owner-name").value.trim(),
      name: $("#f-name").value.trim() || "我的课表",
      term_start: $("#f-term").value.trim(),
      total_weeks: Number($("#f-weeks").value || 20),
      timezone: $("#f-tz").value.trim() || "Asia/Shanghai",
      source: "控制面板",
      courses,
      leaves: readLeaves(),
      adjustments: state.adjustments,
      new_adjustments: newAdjustments,
      makeup_days: readMakeupDays(),
      groups: groupBinding.groups,
      group_origins: groupBinding.origins,
    };
    if (!payload.owner_id) return toast("请填写用户 ID");
    if (!payload.term_start) return toast("请填写开学日期");
    try {
      await apiPost("timetable/save", payload);
      toast("已保存");
      closeEditor();
      await Promise.all([loadRecords(), loadOverview()]);
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  }

  async function onImportFile(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    try {
      if (typeof file.text === "function") {
        $("#import-text").value = await file.text();
      } else {
        $("#import-text").value = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => reject(new Error("读取失败"));
          reader.readAsText(file);
        });
      }
      toast(`已读取 ${file.name}，点击导入完成解析`);
    } catch (err) {
      toast(`读取文件失败：${err.message}`);
    }
  }

  async function importData() {
    const payload = {
      platform: $("#import-platform").value.trim() || "aiocqhttp",
      owner_id: $("#import-owner-id").value.trim(),
      owner_name: $("#import-owner-name").value.trim(),
      text: $("#import-text").value.trim(),
    };
    if (!payload.owner_id) return toast("请填写用户 ID");
    if (!payload.text) return toast("请粘贴课表内容或选择文件");
    const button = $("#import-submit");
    button.disabled = true;
    try {
      const data = await apiPost("import", payload);
      toast(
        `已导入「${data.name || payload.owner_id}」：` +
        `${data.distinct ?? data.course_count ?? 0} 门课 / ${data.course_count ?? 0} 条安排`
      );
      $("#import-text").value = "";
      loadRecords();
      loadOverview();
    } catch (err) {
      toast(`导入失败：${err.message}`);
    } finally {
      button.disabled = false;
    }
  }

  async function deleteRecord(key) {
    if (!key) return;
    const ok = await askConfirm(
      `确定删除 ${key} 的课表吗？请假、调课和群绑定也会一起删除，此操作不可撤销。`,
      "删除课表",
    );
    if (!ok) return;
    try {
      await apiPost("timetable/delete", { key });
      toast("已删除");
      if (state.editingKey === key) closeEditor();
      loadRecords();
    } catch (err) {
      toast(`删除失败：${err.message}`);
    }
  }

  /* ---------------------------------------------------------------- holiday */

  async function loadHoliday() {
    $("#holiday-year").value = state.holidayYear;
    try {
      const data = await apiGet("holidays", { year: state.holidayYear });
      $("#holiday-source").textContent = data.source
        ? `来源：${data.source} · 更新于 ${data.updated_at || "未知"}`
        : "还没有本地缓存，点击「刷新缓存」拉取";
      const days = data.days || [];
      $("#holiday-list").innerHTML = days.length
        ? days.map((day) => `
          <div class="holiday-item ${day.off ? "" : "work"}">
            <b>${esc(day.date)} · ${day.off ? "放假" : "补班"}</b>
            <small>${esc(day.name || "")}</small>
          </div>`).join("")
        : '<div class="empty">没有缓存数据</div>';
    } catch (err) {
      toast(`节假日读取失败：${err.message}`);
    }
  }

  async function refreshHoliday() {
    const year = Number($("#holiday-year").value || state.holidayYear);
    state.holidayYear = year;
    try {
      const data = await apiPost("holidays/refresh", { year });
      toast(`已更新 ${data.count || 0} 天数据`);
      loadHoliday();
    } catch (err) {
      toast(`刷新失败：${err.message}`);
    }
  }

  /* ---------------------------------------------------------------- settings */

  async function loadSettings() {
    try {
      const data = await apiGet("settings");
      state.settings = data || {};
      const set = (id, key) => {
        const el = $(id);
        if (el.type === "checkbox") el.checked = Boolean(data[key]);
        else el.value = data[key] ?? "";
      };
      set("#set-store-teacher", "store_teacher");
      set("#set-store-location", "store_location");
      set("#set-everyone-image", "everyone_image");
      set("#set-avatar", "avatar_enabled");
      set("#set-help-image", "help_image");
      set("#set-hide-idle", "everyone_hide_idle_over");
      set("#set-page-size", "everyone_page_size");
      set("#set-showcase", "showcase_enabled");
      set("#set-showcase-time", "showcase_time");
      set("#set-showcase-min", "showcase_min_minutes");
      set("#set-showcase-users", "showcase_min_users");
      set("#set-recap", "recap_enabled");
      set("#set-recap-weekday", "recap_weekday");
      set("#set-recap-time", "recap_time");
      set("#set-recap-mode", "recap_mode");
      set("#set-recap-top", "recap_top");
      set("#set-holiday-enabled", "holiday_enabled");
      set("#set-holiday-url", "holiday_api_url");
      set("#set-holiday-hours", "holiday_cache_hours");
      set("#set-makeup-map", "makeup_weekday_map");
      set("#set-early", "early_enabled");
      set("#set-early-time", "early_time");
      set("#set-early-max", "early_max_start");
      set("#set-early-at", "early_at");
      try {
        const events = await apiGet("events");
        renderEvents((events && events.items) || []);
      } catch (err) {
        renderEvents([]);
      }
    } catch (err) {
      toast(`设置读取失败：${err.message}`);
    }
  }

  function eventRow(item = {}) {
    return `
      <div class="record-row event-row">
        <label class="grow">名称<input class="input" data-field="name" value="${esc(item.name || "")}" placeholder="CET-6 / 期末考试"></label>
        <label>日期<input class="input" type="date" data-field="date" value="${esc(item.date || "")}"></label>
        <button class="icon-btn remove" title="移除">✕</button>
      </div>`;
  }

  function renderEvents(items = []) {
    const wrap = $("#event-editor");
    wrap.innerHTML = items.length ? items.map(eventRow).join("") : "";
    bindRecordRemoval("#event-editor");
  }

  function readEvents() {
    const items = [];
    for (const row of document.querySelectorAll("#event-editor .event-row")) {
      const name = row.querySelector('[data-field="name"]')?.value.trim() || "";
      const day = row.querySelector('[data-field="date"]')?.value.trim() || "";
      if (!name || !day) continue;
      items.push({ name, date: day });
    }
    return items;
  }

  async function saveSettings() {
    const num = (id) => {
      const n = Number($(id).value);
      return Number.isFinite(n) ? n : 0;
    };
    const payload = {
      store_teacher: $("#set-store-teacher").checked,
      store_location: $("#set-store-location").checked,
      everyone_image: $("#set-everyone-image").checked,
      avatar_enabled: $("#set-avatar").checked,
      help_image: $("#set-help-image").checked,
      everyone_hide_idle_over: num("#set-hide-idle"),
      everyone_page_size: num("#set-page-size"),
      showcase_enabled: $("#set-showcase").checked,
      showcase_time: $("#set-showcase-time").value.trim(),
      showcase_min_minutes: num("#set-showcase-min"),
      showcase_min_users: num("#set-showcase-users"),
      recap_enabled: $("#set-recap").checked,
      recap_weekday: num("#set-recap-weekday"),
      recap_time: $("#set-recap-time").value.trim(),
      recap_mode: $("#set-recap-mode").value,
      recap_top: num("#set-recap-top"),
      holiday_enabled: $("#set-holiday-enabled").checked,
      holiday_api_url: $("#set-holiday-url").value.trim(),
      holiday_cache_hours: num("#set-holiday-hours"),
      makeup_weekday_map: $("#set-makeup-map").value,
      early_enabled: $("#set-early").checked,
      early_time: $("#set-early-time").value.trim(),
      early_max_start: $("#set-early-max").value.trim(),
      early_at: $("#set-early-at").checked,
    };
    try {
      await apiPost("settings/save", payload);
      try {
        await apiPost("events/save", { items: readEvents() });
      } catch (err) {
        toast(`重要日期保存失败：${err.message}`);
      }
      toast("设置已保存");
      loadOverview();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  }

  async function exportBackup() {
    try {
      const data = await apiGet("backup");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `schedule-lite-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast("备份已导出");
    } catch (err) {
      toast(`备份导出失败：${err.message}`);
    }
  }

  async function onRestoreFile(event) {
    const input = event.target;
    const file = input.files && input.files[0];
    input.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== "object" || !parsed.users) {
        throw new Error("不是本插件的备份文件");
      }
      state.restorePayload = parsed;
      const count = Object.keys(parsed.users).length;
      $("#restore-hint").textContent = `已读取 ${file.name}：${count} 份课表、${(parsed.events || []).length} 个重要日期。`;
      $("#backup-restore").disabled = false;
    } catch (err) {
      state.restorePayload = null;
      $("#backup-restore").disabled = true;
      $("#restore-hint").textContent = "还没有选择备份文件。";
      toast(`读取备份失败：${err.message}`);
    }
  }

  async function restoreBackup() {
    const payload = state.restorePayload;
    if (!payload) return;
    const ok = await askConfirm(
      "恢复备份会覆盖当前全部课表和重要日期，确定继续吗？",
      "恢复备份",
    );
    if (!ok) return;
    try {
      const data = await apiPost("restore", payload);
      toast(`已恢复 ${data.restored} 份课表`);
      state.restorePayload = null;
      $("#backup-restore").disabled = true;
      $("#restore-hint").textContent = "还没有选择备份文件。";
      loadRecords();
      loadOverview();
    } catch (err) {
      toast(`恢复失败：${err.message}`);
    }
  }

  /* ---------------------------------------------------------------- bind */

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });
  $("#refresh-live").addEventListener("click", loadLive);
  $("#search").addEventListener("input", () => {
    clearTimeout(loadRecords.timer);
    loadRecords.timer = setTimeout(loadRecords, 220);
  });
  $("#add-record").addEventListener("click", () => openEditor(""));
  $("#refresh-records").addEventListener("click", () => {
    loadRecords();
    loadOverview();
  });
  $("#import-pick").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", onImportFile);
  $("#import-submit").addEventListener("click", importData);
  $("#editor-close").addEventListener("click", closeEditor);
  $("#cancel-edit").addEventListener("click", closeEditor);
  $("#editor-mask").addEventListener("click", (event) => {
    if (event.target === $("#editor-mask")) closeEditor();
  });
  $("#add-course").addEventListener("click", () => {
    const wrap = $("#course-editor");
    if (wrap.querySelector(".empty")) wrap.innerHTML = "";
    wrap.insertAdjacentHTML("beforeend", courseRow({ day: 1, weeks: "1-16", start_section: 1, end_section: 2 }));
    wrap.lastElementChild.querySelector(".remove").addEventListener("click", (event) => {
      event.currentTarget.closest(".course-row").remove();
    });
  });
  $("#save-record").addEventListener("click", saveRecord);
  $("#delete-record").addEventListener("click", () => deleteRecord(state.editingKey));

  function appendRecord(selector, html) {
    const wrap = $(selector);
    wrap.insertAdjacentHTML("beforeend", html);
    wrap.lastElementChild.querySelector(".remove").addEventListener("click", (event) => {
      event.currentTarget.closest(".record-row").remove();
    });
  }

  $("#add-leave").addEventListener("click", () => appendRecord("#leave-editor", leaveRow()));
  $("#add-adjust").addEventListener("click", () => appendRecord("#adjust-new", newAdjustmentRow()));
  $("#add-makeup").addEventListener("click", () => appendRecord("#makeup-editor", makeupRow()));
  $("#add-group").addEventListener("click", () => appendRecord("#group-editor", groupRow()));
  $("#add-event").addEventListener("click", () => appendRecord("#event-editor", eventRow()));

  $("#backup-export").addEventListener("click", exportBackup);
  $("#backup-pick").addEventListener("click", () => $("#restore-file").click());
  $("#restore-file").addEventListener("change", onRestoreFile);
  $("#backup-restore").addEventListener("click", restoreBackup);

  $("#confirm-cancel").addEventListener("click", () => resolveConfirm(false));
  $("#confirm-ok").addEventListener("click", () => resolveConfirm(true));
  $("#confirm-mask").addEventListener("click", (event) => {
    if (event.target === $("#confirm-mask")) resolveConfirm(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#confirm-mask").hidden) resolveConfirm(false);
  });

  $("#load-holiday").addEventListener("click", () => {
    state.holidayYear = Number($("#holiday-year").value || state.holidayYear);
    loadHoliday();
  });
  $("#refresh-holiday").addEventListener("click", refreshHoliday);
  $("#save-settings").addEventListener("click", saveSettings);

  $("#theme-toggle").addEventListener("click", cycleTheme);
  $("#conn-text").addEventListener("click", async () => {
    if (!usableBridge(cachedBridge)) {
      cachedBridge = null;
      bridgeProbed = false;
      await ready();
      switchView(state.view);
    }
  });
  applyTheme(readThemeMode());
  if (themeQuery) {
    const onSystemTheme = () => {
      if (state.themeMode === "auto") applyTheme("auto");
    };
    if (typeof themeQuery.addEventListener === "function") {
      themeQuery.addEventListener("change", onSystemTheme);
    } else if (typeof themeQuery.addListener === "function") {
      themeQuery.addListener(onSystemTheme);
    }
  }

  (async function boot() {
    await ready();
    switchView("overview");
  })();
})();
