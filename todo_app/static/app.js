"use strict";

// All user text is rendered with textContent; never build HTML from strings.
(() => {
  const byId = (id) => {
    const el = document.getElementById(id);
    if (!el) throw new Error(`#${id} がありません`);
    return el;
  };

  const els = {
    monthLabel: byId("month-label"),
    prev: byId("prev-month"),
    next: byId("next-month"),
    todayBtn: byId("today-btn"),
    grid: byId("calendar-grid"),
    message: byId("message"),
    overdueSection: byId("overdue-section"),
    overdueList: byId("overdue-list"),
    selectedLabel: byId("selected-label"),
    taskForm: byId("task-form"),
    taskTitle: byId("task-title"),
    taskList: byId("task-list"),
    taskEmpty: byId("task-empty"),
    journalText: byId("journal-text"),
    journalStatus: byId("journal-status"),
    journalCount: byId("journal-count"),
    journalSave: byId("journal-save"),
  };

  const JOURNAL_MAX = 10000;
  const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

  const state = {
    today: "",
    year: 0,
    month: 0,
    selected: "",
    journalSaved: "",
    journalEditable: true,
    // Sequence numbers drop responses that arrive after a newer request.
    calendarSeq: 0,
    daySeq: 0,
  };

  // ---------------------------------------------------------------- utils
  const pad = (n) => String(n).padStart(2, "0");
  const monthKey = (y, m) => `${y}-${pad(m)}`;

  function parseYmd(s) {
    const [y, m, d] = s.split("-").map(Number);
    return { y, m, d };
  }

  function dateLabel(s) {
    const { y, m, d } = parseYmd(s);
    // Date.UTC avoids local-timezone shifts when computing the weekday.
    const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return `${y}年${m}月${d}日（${WEEKDAYS[wd]}）`;
  }

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
  }

  let messageTimer = 0;
  function showMessage(text, isError = true) {
    els.message.textContent = text;
    els.message.classList.toggle("error", isError);
    els.message.hidden = false;
    clearTimeout(messageTimer);
    messageTimer = setTimeout(() => { els.message.hidden = true; }, isError ? 6000 : 2500);
  }

  async function api(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, options);
    } catch {
      throw new Error("サーバーに接続できません。アプリが起動しているか確認してください");
    }
    let data = null;
    try {
      data = await res.json();
    } catch {
      // Non-JSON response; handled below.
    }
    if (!res.ok) throw new Error((data && data.error) || `エラーが発生しました（${res.status}）`);
    if (data === null) throw new Error("サーバーの応答が不正です");
    return data;
  }

  // Runs an action with a control disabled, reporting errors to the user.
  async function guarded(control, action) {
    if (control) control.disabled = true;
    try {
      return await action();
    } catch (err) {
      showMessage(err.message);
      return undefined;
    } finally {
      if (control) control.disabled = false;
    }
  }

  // ------------------------------------------------------------- calendar
  async function loadCalendar() {
    const seq = ++state.calendarSeq;
    const data = await api("GET", `/api/calendar?month=${monthKey(state.year, state.month)}`);
    if (seq !== state.calendarSeq) return;
    state.today = data.today;
    renderCalendar(data.days);
  }

  function renderCalendar(days) {
    els.monthLabel.textContent = `${state.year}年${state.month}月`;
    const cells = days.map((day, i) => {
      const classes = ["cell"];
      if (!day.in_month) classes.push("other-month");
      if (day.is_today) classes.push("today");
      if (day.date === state.selected) classes.push("selected");
      if (i % 7 === 0) classes.push("sun");
      if (i % 7 === 6) classes.push("sat");

      const badges = [];
      if (day.overdue) badges.push(el("span", { class: "badge late", text: "!" }));
      if (day.open_count > 0) badges.push(el("span", { class: "badge open", text: `未${day.open_count}` }));
      if (day.done_count > 0) badges.push(el("span", { class: "badge done", text: `済${day.done_count}` }));
      if (day.has_journal) badges.push(el("span", { class: "badge journal", text: "誌" }));

      const label = [dateLabel(day.date)];
      if (day.open_count) label.push(`未完了${day.open_count}件`);
      if (day.done_count) label.push(`完了${day.done_count}件`);
      if (day.overdue) label.push("期限切れあり");
      if (day.has_journal) label.push("日誌あり");

      return el("button", {
        type: "button",
        class: classes.join(" "),
        "aria-label": label.join("、"),
        "aria-pressed": String(day.date === state.selected),
        onclick: () => selectDate(day.date),
      }, [el("span", { class: "day-num", text: String(day.day) }), el("span", { class: "badges" }, badges)]);
    });
    els.grid.replaceChildren(...cells);
  }

  function changeMonth(delta) {
    let m = state.month + delta;
    let y = state.year;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    if (y < 1900 || y > 2999) return;
    state.year = y;
    state.month = m;
    guarded(null, loadCalendar);
  }

  // ------------------------------------------------------------- overdue
  async function loadOverdue() {
    const data = await api("GET", "/api/tasks/overdue");
    els.overdueList.replaceChildren(...data.tasks.map((task) => el("li", {}, [
      el("button", {
        type: "button",
        class: "link",
        text: `${task.due_date}  ${task.title}`,
        onclick: () => selectDate(task.due_date),
      }),
    ])));
    els.overdueSection.hidden = data.tasks.length === 0;
  }

  // ------------------------------------------------------------- day panel
  function journalDirty() {
    return state.journalEditable && els.journalText.value !== state.journalSaved;
  }

  function selectDate(date) {
    if (date === state.selected) return;
    if (journalDirty() && !confirm("保存していない日誌があります。破棄して移動しますか？")) return;
    state.selected = date;
    const { y, m } = parseYmd(date);
    state.year = y;
    state.month = m;
    guarded(null, loadCalendar);  // re-renders the selection highlight too
    guarded(null, loadDay);
  }

  async function loadDay() {
    const seq = ++state.daySeq;
    const date = state.selected;
    els.selectedLabel.textContent = dateLabel(date);
    const [tasks, journal] = await Promise.all([
      api("GET", `/api/tasks?from=${date}&to=${date}`),
      api("GET", `/api/journal/${date}`),
    ]);
    if (seq !== state.daySeq) return;
    renderTasks(tasks.tasks);
    renderJournal(journal);
  }

  async function refreshAfterChange() {
    await Promise.all([loadCalendar(), loadOverdue(), reloadTasks()]);
  }

  async function reloadTasks() {
    const seq = state.daySeq;
    const date = state.selected;
    const data = await api("GET", `/api/tasks?from=${date}&to=${date}`);
    if (seq === state.daySeq && date === state.selected) renderTasks(data.tasks);
  }

  function renderTasks(tasks) {
    els.taskList.replaceChildren(...tasks.map(taskItem));
    els.taskEmpty.hidden = tasks.length > 0;
  }

  function taskItem(task) {
    const li = el("li", { class: task.done ? "task done" : "task" });

    const checkbox = el("input", {
      type: "checkbox",
      checked: task.done,
      "aria-label": `${task.title} を${task.done ? "未完了に戻す" : "完了にする"}`,
      onchange: async () => {
        const ok = await guarded(checkbox, async () => {
          await api("PATCH", `/api/tasks/${task.id}`, { done: checkbox.checked });
          await refreshAfterChange();
          return true;
        });
        if (!ok) checkbox.checked = task.done;
      },
    });

    const title = el("span", { class: "title", text: task.title });
    const notes = task.notes ? el("p", { class: "notes", text: task.notes }) : null;

    const editBtn = el("button", { type: "button", class: "secondary small", text: "編集", onclick: () => startEdit(li, task) });
    const deleteBtn = el("button", {
      type: "button",
      class: "danger small",
      text: "削除",
      onclick: () => {
        if (!confirm(`「${task.title}」を削除しますか？`)) return;
        guarded(deleteBtn, async () => {
          await api("DELETE", `/api/tasks/${task.id}`);
          await refreshAfterChange();
          showMessage("削除しました", false);
        });
      },
    });

    li.append(el("div", { class: "row" }, [checkbox, title, editBtn, deleteBtn]));
    if (notes) li.append(notes);
    return li;
  }

  function startEdit(li, task) {
    const titleInput = el("input", { type: "text", value: task.title, maxLength: 200, required: true, "aria-label": "タイトル" });
    const dateInput = el("input", { type: "date", value: task.due_date, required: true, min: "1900-01-01", max: "2999-12-31", "aria-label": "期日" });
    const notesInput = el("textarea", { rows: 2, maxLength: 2000, placeholder: "メモ（任意）", "aria-label": "メモ" });
    notesInput.value = task.notes;
    const saveBtn = el("button", { type: "submit", class: "small", text: "保存" });
    const cancelBtn = el("button", { type: "button", class: "secondary small", text: "取消", onclick: () => guarded(null, reloadTasks) });

    const form = el("form", {
      class: "edit-form",
      onsubmit: (event) => {
        event.preventDefault();
        if (!titleInput.value.trim()) { showMessage("タイトルを入力してください"); return; }
        if (!dateInput.value) { showMessage("期日を入力してください"); return; }
        guarded(saveBtn, async () => {
          await api("PATCH", `/api/tasks/${task.id}`, {
            title: titleInput.value,
            due_date: dateInput.value,
            notes: notesInput.value,
          });
          await refreshAfterChange();
          showMessage("更新しました", false);
        });
      },
    }, [titleInput, dateInput, notesInput, el("div", { class: "row" }, [saveBtn, cancelBtn])]);

    li.replaceChildren(form);
    titleInput.focus();
  }

  async function addTask(event) {
    event.preventDefault();
    const title = els.taskTitle.value.trim();
    if (!title) { showMessage("タイトルを入力してください"); return; }
    // Clear right away so the next task can be typed (and a double Enter cannot
    // submit twice); the text comes back if saving fails.
    els.taskTitle.value = "";
    const saved = await guarded(null, async () => {
      await api("POST", "/api/tasks", { title, due_date: state.selected });
      return true;
    });
    if (!saved && !els.taskTitle.value) els.taskTitle.value = title;
    await guarded(null, refreshAfterChange);
  }

  // --------------------------------------------------------------- journal
  function renderJournal(data) {
    state.journalSaved = data.entry ? data.entry.content : "";
    state.journalEditable = data.editable;
    els.journalText.value = state.journalSaved;
    els.journalText.disabled = !data.editable;
    els.journalSave.disabled = !data.editable;
    if (!data.editable) setJournalStatus("未来の日付の日誌は書けません");
    else if (data.entry) setJournalStatus(`保存済み（${data.entry.updated_at.replace("T", " ")}）`);
    else setJournalStatus("まだ書かれていません");
    updateJournalCount();
  }

  function setJournalStatus(text) {
    els.journalStatus.textContent = text;
  }

  function updateJournalCount() {
    els.journalCount.textContent = `${els.journalText.value.length} / ${JOURNAL_MAX}`;
  }

  async function saveJournal() {
    if (!state.journalEditable) return;
    const date = state.selected;
    const content = els.journalText.value;
    await guarded(els.journalSave, async () => {
      const data = await api("PUT", `/api/journal/${date}`, { content });
      if (date !== state.selected) return;
      const saved = data.entry ? data.entry.content : "";
      // Text typed while the request was in flight stays as an unsaved change.
      if (els.journalText.value === content) els.journalText.value = saved;
      state.journalSaved = saved;
      if (journalDirty()) setJournalStatus("未保存の変更があります");
      else setJournalStatus(data.entry ? "保存しました" : "日誌を削除しました（空で保存）");
      updateJournalCount();
      await loadCalendar();
    });
  }

  // ------------------------------------------------------------------ init
  function bindEvents() {
    els.prev.addEventListener("click", () => changeMonth(-1));
    els.next.addEventListener("click", () => changeMonth(1));
    els.todayBtn.addEventListener("click", () => {
      if (state.today) selectDate(state.today);
    });
    els.taskForm.addEventListener("submit", addTask);
    els.journalSave.addEventListener("click", saveJournal);
    els.journalText.addEventListener("input", () => {
      updateJournalCount();
      setJournalStatus(journalDirty() ? "未保存の変更があります" : "");
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveJournal();
      }
    });
    window.addEventListener("beforeunload", (event) => {
      if (journalDirty()) event.preventDefault();
    });
  }

  async function init() {
    bindEvents();
    const { today } = await api("GET", "/api/today");
    state.today = today;
    state.selected = today;
    const { y, m } = parseYmd(today);
    state.year = y;
    state.month = m;
    await Promise.all([loadCalendar(), loadDay(), loadOverdue()]);
  }

  init().catch((err) => showMessage(err.message));
})();
