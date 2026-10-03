const state = {
  token: "",
  user: "",
  school: "",
  lessons: [],
  rows: [],
  selectedDate: startOfToday(),
  weekStart: mondayOf(startOfToday()),
  dayMode: true,
  tab: "plan",
  days: 7,
};

const $ = (id) => document.getElementById(id);
const tabs = Array.from(document.querySelectorAll(".chip[data-tab]"));
const weekdaysShort = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const weekdaysLong = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

function apiUrl(path) {
  return new URL(`../${path.replace(/^\/+/, "")}`, window.location.href).toString();
}

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

function mondayOf(date) {
  const d = new Date(date);
  const day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  d.setHours(0, 0, 0, 0);
  return d;
}

function addDays(date, days) {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

function iso(date) {
  return date.toISOString().slice(0, 10);
}

function dm(date) {
  return `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}.`;
}

function parseDate(value) {
  if (!value) return null;
  const d = new Date(String(value).slice(0, 10));
  return Number.isNaN(d.getTime()) ? null : d;
}

function timeOf(value) {
  return (value || "").trim().split(" ").pop() || "";
}

async function request(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(apiUrl(path), { ...options, headers });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function setMessage(text, level = "muted") {
  const el = $("loginMessage");
  el.textContent = text || "";
  el.style.color = level === "error" ? "var(--red)" : level === "ok" ? "var(--lime)" : "var(--muted)";
}

function setNotice(text) {
  const el = $("notice");
  el.textContent = text || "";
  el.classList.toggle("hidden", !text);
}

async function loadOptions() {
  try {
    const payload = await request("/api/addon/options");
    const data = payload.data || {};
    $("serverInput").value = data.server || "demo.local";
    $("schoolInput").value = data.school || "demo";
    $("userInput").value = data.username || "Demo";
    $("passwordInput").value = data.password || "Demo";
    state.days = Number(data.days || 7);
    if (data.auto_login) await login();
  } catch {
    $("serverInput").value = "demo.local";
    $("schoolInput").value = "demo";
    $("userInput").value = "Demo";
    $("passwordInput").value = "Demo";
  }
}

async function login() {
  setMessage("Anmeldung laeuft ...");
  const payload = {
    server: $("serverInput").value.trim(),
    school: $("schoolInput").value.trim(),
    username: $("userInput").value.trim(),
    password: $("passwordInput").value,
  };
  const result = await request("/api/auth/validate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!result.status) {
    setMessage(result.message || "Anmeldung fehlgeschlagen", "error");
    return;
  }
  state.token = result.access_token;
  state.user = result.user_name || payload.username;
  state.school = payload.school;
  $("subtitle").textContent = `${state.user} · ${state.school}`;
  $("loginPanel").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  setMessage("");
  await reloadAll();
}

async function reloadAll() {
  if (!state.token) return;
  setNotice("Aktualisiere ...");
  await Promise.all([
    loadTimetable(),
    loadRowsForTab(state.tab),
  ]);
  setNotice("");
  render();
}

async function loadTimetable() {
  const start = iso(state.weekStart);
  const end = iso(addDays(state.weekStart, Math.max(6, state.days - 1)));
  const payload = await request(`/api/users/my/timetable?start=${start}&end=${end}&days=${state.days}`);
  if (!payload.status) throw new Error(payload.message || "Stundenplan konnte nicht geladen werden");
  state.lessons = payload.data || [];
}

async function loadRowsForTab(tab) {
  const map = {
    substitutions: "/api/data/substitutions",
    exams: "/api/data/exams",
    homework: "/api/data/homework",
    messages: "/api/data/messages",
    holidays: "/api/data/holidays",
    teachers: "/api/data/teachers",
  };
  if (!map[tab]) {
    state.rows = [];
    return;
  }
  const payload = await request(map[tab]);
  state.rows = payload.status ? payload.data || [] : [];
}

function render() {
  renderTabs();
  renderWeek();
  if (state.tab === "plan") renderPlan();
  else renderRows();
}

function renderTabs() {
  tabs.forEach((button) => button.classList.toggle("active", button.dataset.tab === state.tab));
  $("dayModeBtn").classList.toggle("active", state.dayMode);
  $("weekModeBtn").classList.toggle("active", !state.dayMode);
}

function renderWeek() {
  const end = addDays(state.weekStart, 6);
  $("weekLabel").textContent = `${dm(state.weekStart)} - ${dm(end)}${end.getFullYear()}`;
  const byDate = groupLessons();
  $("dayStrip").innerHTML = "";
  for (let i = 0; i < 7; i += 1) {
    const day = addDays(state.weekStart, i);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "day-button";
    button.classList.toggle("today", iso(day) === iso(startOfToday()));
    button.classList.toggle("active", iso(day) === iso(state.selectedDate));
    button.innerHTML = `<span class="day-name">${weekdaysShort[i]}</span><span class="day-number">${day.getDate()}</span><span class="day-dot">${byDate.get(iso(day))?.length ? "•" : "&nbsp;"}</span>`;
    button.addEventListener("click", () => {
      state.selectedDate = day;
      state.dayMode = true;
      render();
    });
    $("dayStrip").appendChild(button);
  }
}

function groupLessons() {
  const map = new Map();
  for (const lesson of state.lessons) {
    const key = (lesson.date || "").slice(0, 10);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(lesson);
  }
  for (const list of map.values()) {
    list.sort((a, b) => timeOf(a.startTime).localeCompare(timeOf(b.startTime)));
  }
  return map;
}

function renderPlan() {
  const content = $("content");
  const byDate = groupLessons();
  content.innerHTML = "";
  if (!state.dayMode) {
    const grid = document.createElement("div");
    grid.className = "week-grid";
    for (let i = 0; i < 5; i += 1) {
      const day = addDays(state.weekStart, i);
      const col = document.createElement("div");
      col.className = "week-column";
      col.innerHTML = `<div class="week-heading">${weekdaysLong[i]} ${dm(day)}</div>`;
      const list = byDate.get(iso(day)) || [];
      if (!list.length) col.appendChild(emptyNode("Keine Stunden"));
      list.forEach((lesson) => col.appendChild(miniLessonNode(lesson)));
      grid.appendChild(col);
    }
    content.appendChild(grid);
    return;
  }

  const list = byDate.get(iso(state.selectedDate)) || [];
  if (!list.length) {
    content.appendChild(emptyNode("Keine Stunden"));
    return;
  }
  list.forEach((lesson) => content.appendChild(lessonNode(lesson)));
}

function lessonNode(lesson) {
  const node = document.createElement("article");
  const changed = lesson.substitution || lesson.allDay;
  node.className = `lesson-card ${lesson.cancelled ? "cancelled" : ""} ${lesson.exam ? "exam" : ""} ${changed ? "changed" : ""}`;
  const title = lesson.allDay ? lesson.title || lesson.subject || "-" : lesson.subjectFull || lesson.subject || "-";
  const teacher = lesson.teacherFull || lesson.teacher || "";
  const badge = lesson.cancelled
    ? `<div class="badge red">Entfaellt</div>`
    : lesson.exam
      ? `<div class="badge amber">Pruefung</div>`
      : changed
        ? `<div class="badge green">${lesson.allDay ? "Termin" : "Vertretung"}</div>`
        : "";
  node.innerHTML = `
    <div>
      <div class="time-start">${lesson.allDay ? "Ganztag" : timeOf(lesson.startTime)}</div>
      <div class="time-end">${lesson.allDay ? "" : lesson.endTime || ""}</div>
    </div>
    <div>
      <div class="lesson-title">${escapeHtml(title)}</div>
      <div class="subline">${escapeHtml(teacher)}</div>
      ${badge}
    </div>
    ${lesson.classroom ? `<div class="room">${escapeHtml(lesson.classroom)}</div>` : ""}
  `;
  return node;
}

function miniLessonNode(lesson) {
  const node = document.createElement("article");
  node.className = "mini-card";
  const title = lesson.allDay ? lesson.title || lesson.subject || "-" : lesson.subject || "-";
  node.innerHTML = `
    <div class="mini-time">${lesson.allDay ? "Ganztag" : timeOf(lesson.startTime)}</div>
    <div class="mini-title">${escapeHtml(title)}</div>
    <div class="subline">${escapeHtml(lesson.teacher || "")}</div>
    <div class="mini-room">${escapeHtml(lesson.classroom || "")}</div>
  `;
  return node;
}

function renderRows() {
  const content = $("content");
  content.innerHTML = "";
  if (!state.rows.length) {
    content.appendChild(emptyNode("Keine Daten"));
    return;
  }
  for (const row of state.rows) content.appendChild(rowNode(row));
}

function rowNode(row) {
  const node = document.createElement("article");
  node.className = "row-card";
  const title = row.subject || row.name || row.fullName || row.title || row.sender || row.date || "-";
  const parts = [
    row.date || row.startDate,
    row.endDate,
    row.teacher || row.shortName || row.originalTeacher,
    row.substituteTeacher,
    row.rooms || row.classroom,
    row.text,
  ].filter(Boolean);
  node.innerHTML = `<div class="row-title">${escapeHtml(title)}</div><div class="subline">${escapeHtml(parts.join(" · "))}</div>`;
  return node;
}

function emptyNode(text) {
  const node = document.createElement("div");
  node.className = "empty";
  node.textContent = text;
  return node;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[c]));
}

$("loginBtn").addEventListener("click", () => login().catch((err) => setMessage(err.message, "error")));
$("demoBtn").addEventListener("click", () => {
  $("serverInput").value = "demo.local";
  $("schoolInput").value = "demo";
  $("userInput").value = "Demo";
  $("passwordInput").value = "Demo";
});
$("refreshBtn").addEventListener("click", () => reloadAll().catch((err) => setNotice(err.message)));
$("prevWeek").addEventListener("click", async () => {
  state.weekStart = addDays(state.weekStart, -7);
  state.selectedDate = state.weekStart;
  await reloadAll();
});
$("nextWeek").addEventListener("click", async () => {
  state.weekStart = addDays(state.weekStart, 7);
  state.selectedDate = state.weekStart;
  await reloadAll();
});
$("todayBtn").addEventListener("click", async () => {
  state.selectedDate = startOfToday();
  state.weekStart = mondayOf(state.selectedDate);
  state.dayMode = true;
  await reloadAll();
});
$("dayModeBtn").addEventListener("click", () => {
  state.dayMode = true;
  render();
});
$("weekModeBtn").addEventListener("click", () => {
  state.dayMode = false;
  render();
});
tabs.forEach((button) => button.addEventListener("click", async () => {
  state.tab = button.dataset.tab;
  await loadRowsForTab(state.tab);
  render();
}));

loadOptions();
