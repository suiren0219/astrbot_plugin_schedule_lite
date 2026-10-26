(() => {
  "use strict";

  const bridge = window.AstrBotPluginView || window.AstrBotPluginPage || null;
  const $ = (sel) => document.querySelector(sel);
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
    settings: {},
    holidayYear: new Date().getFullYear(),
    themeMode: "auto",
    bridgeDark: null,
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

  async function ready() {
    if (bridge) {
      try {
        const ctx = await bridge.ready();
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
    $("#conn-text").textContent = "预览模式";
    $("#conn-dot").classList.add("off");
  }

  async function apiGet(path, params = {}) {
    if (!bridge) return demo(path, params);
    return bridge.apiGet(`page/${path}`, params);
  }
  async function apiPost(path, body = {}) {
    if (!bridge) return demo(path, body);
    return bridge.apiPost(`page/${path}`, body);
  }

  /* ---------------------------------------------------------------- demo */

  const demoRecords = [
    {
      key: "aiocqhttp:10001", owner_id: "10001", owner_name: "张三", name: "默认课表",
      term_start: "2026-09-07", total_weeks: 20, timezone: "Asia/Shanghai",
      course_count: 12, groups: ["100000"], notify_enabled: true,
      leave_count: 1, adjustment_count: 0, makeup_count: 0,
    },
    {
      key: "aiocqhttp:10002", owner_id: "10002", owner_name: "李四", name: "示例班级",
      term_start: "2026-09-07", total_weeks: 18, timezone: "Asia/Shanghai",
      course_count: 9, groups: ["100000"], notify_enabled: false,
      leave_count: 0, adjustment_count: 2, makeup_count: 1,
    },
  ];
  const demoDetail = {
    key: "aiocqhttp:10001",
    owner_id: "10001", owner_name: "张三", name: "默认课表",
    term_start: "2026-09-07", total_weeks: 20, timezone: "Asia/Shanghai",
    sections: [
      { index: 1, start: "08:30", end: "09:15" },
      { index: 2, start: "09:20", end: "10:05" },
      { index: 3, start: "10:25", end: "11:10" },
      { index: 4, start: "11:15", end: "12:00" },
      { index: 5, start: "14:40", end: "15:25" },
      { index: 6, start: "15:30", end: "16:15" },
    ],
    courses: [
      { name: "大学物理实验", day: 1, weeks: "10", start: "", end: "", start_section: 1, end_section: 2 },
      { name: "模拟电子技术A", day: 2, weeks: "1-16", start: "", end: "", start_section: 3, end_section: 4 },
      { name: "线性代数", day: 4, weeks: "1-16", start: "14:40", end: "16:15" },
    ],
  };

  function demo(path, body) {
    if (path === "overview") {
      return {
        users: 2, courses: 21, groups: 1, today: "2026-10-26", weekday: "周一",
        status_counts: { "进行中": 1, "请假中": 1, "下一节": 1, "无课程": 2 },
        holiday: { name: "", off: false, workday: false, source: "timor.tech" },
        config: { store_teacher: false, store_location: false, everyone_image: true, help_image: true },
      };
    }
    if (path === "live") {
      return { items: [
        { name: "张三", type: 1, label: "进行中", main: "大学物理实验", sub: "08:30-10:05（剩余 35分钟）" },
        { name: "李四", type: 6, label: "请假中", main: "今日请假", sub: "病假" },
        { name: "王五", type: 3, label: "下一节", main: "线性代数", sub: "14:40-16:15（3小时50分后）" },
        { name: "赵六", type: 5, label: "无课程", main: "今日无课程", sub: "" },
      ]};
    }
    if (path === "timetables") {
      const q = String((body && body.q) || "").toLowerCase();
      const items = q ? demoRecords.filter((r) => `${r.owner_name}${r.key}`.toLowerCase().includes(q)) : demoRecords;
      return { items, total: items.length };
    }
    if (path === "timetable") return { ...demoDetail, key: (body && body.key) || demoDetail.key };
    if (path === "settings") {
      return {
        store_teacher: false, store_location: false, everyone_image: true, help_image: true,
        everyone_page_size: 8, everyone_hide_idle_over: 12,
        showcase_enabled: true, showcase_time: "22:30", showcase_min_minutes: 300, showcase_min_users: 2,
        recap_enabled: true, recap_weekday: 5, recap_time: "21:00", recap_mode: "week", recap_top: 5,
        holiday_enabled: true, holiday_api_url: "https://timor.tech/api/holiday/year/{year}", holiday_cache_hours: 24,
      };
    }
    if (path === "holidays") {
      return {
        year: 2026, source: "timor.tech", updated_at: "2026-10-26T08:00:00+08:00",
        days: [
          { date: "2026-01-01", name: "元旦", off: true },
          { date: "2026-01-04", name: "元旦后补班", off: false },
          { date: "2026-10-01", name: "国庆节", off: true },
          { date: "2026-10-10", name: "国庆节后补班", off: false },
        ],
      };
    }
    if (path === "timetable/save") return { ...demoDetail, key: body.key || demoDetail.key, ...body };
    if (path === "timetable/delete") return { deleted: body.key };
    if (path === "settings/save") return body;
    if (path === "holidays/refresh") return { year: 2026, source: "timor.tech", updated_at: "now", count: 30 };
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

  async function loadLive() {
    try {
      const data = await apiGet("live");
      const items = data.items || [];
      $("#live-list").innerHTML = items.length
        ? items.map((item) => `
          <div class="live-row">
            <span class="avatar">${esc((item.name || "?").slice(0, 1))}</span>
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

  async function openEditor(key = "") {
    try {
      const data = key ? await apiGet("timetable", { key }) : blankRecord();
      state.editingKey = key;
      $("#editor-title").textContent = key ? "编辑课表" : "新增课表";
      $("#editor-key").textContent = key || "保存后生成 platform:owner_id";
      $("#f-platform").value = key ? key.split(":")[0] : "aiocqhttp";
      $("#f-owner-id").value = data.owner_id || "";
      $("#f-owner-name").value = data.owner_name || "";
      $("#f-name").value = data.name || "我的课表";
      $("#f-term").value = data.term_start || mondayOf();
      $("#f-weeks").value = data.total_weeks || 20;
      $("#f-tz").value = data.timezone || "Asia/Shanghai";
      state.courses = Array.isArray(data.courses) ? data.courses.slice() : [];
      renderCourses();
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
    };
    if (!payload.owner_id) return toast("请填写用户 ID");
    if (!payload.term_start) return toast("请填写开学日期");
    try {
      await apiPost("timetable/save", payload);
      toast("已保存");
      closeEditor();
      loadRecords();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  }

  async function deleteRecord(key) {
    if (!key) return;
    if (!window.confirm(`确定删除 ${key} 的课表吗？此操作不可撤销。`)) return;
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
    } catch (err) {
      toast(`设置读取失败：${err.message}`);
    }
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
    };
    try {
      await apiPost("settings/save", payload);
      toast("设置已保存");
      loadOverview();
    } catch (err) {
      toast(`保存失败：${err.message}`);
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
  $("#load-holiday").addEventListener("click", () => {
    state.holidayYear = Number($("#holiday-year").value || state.holidayYear);
    loadHoliday();
  });
  $("#refresh-holiday").addEventListener("click", refreshHoliday);
  $("#save-settings").addEventListener("click", saveSettings);

  $("#theme-toggle").addEventListener("click", cycleTheme);
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
