const state = {
  token: "",
  user: "",
  school: "",
  lessons: [],
  rows: [],
  selectedSchoolLogin: "",
  selectedServer: "",
  selectedTeacher: "",
  teacherLessons: [],
  unsupported: new Set(),
  lang: "de",
  weekLayout: "portrait",
  schoolSearchTimer: 0,
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

function formatDateDE(value) {
  const text = String(value || "").trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) return `${match[3]}.${match[2]}.${match[1]}`;
  return text.replace(/T00:00:00(?:\.000)?(?:Z)?$/, "");
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
    state.selectedSchoolLogin = data.school || "demo";
    state.selectedServer = data.server || "demo.local";
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
    server: (state.selectedServer || $("serverInput").value).trim(),
    school: (state.selectedSchoolLogin || $("schoolInput").value).trim(),
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
  $("logoutBtn").classList.remove("hidden");
  setMessage("");
  await reloadAll();
}

async function reloadAll() {
  if (!state.token) return;
  setNotice("Aktualisiere ...");
  try {
    await loadTimetable();
    await loadRowsForTab(state.tab);
    setNotice("");
  } catch (err) {
    setNotice(err.message || "Aktualisierung fehlgeschlagen");
  }
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
  if (payload.available === false || payload.message === "UNSUPPORTED") state.unsupported.add(tab);
  else state.unsupported.delete(tab);
  state.rows = payload.status ? payload.data || [] : [];
}

async function searchSchools(query) {
  const results = $("schoolResults");
  const q = query.trim();
  state.selectedSchoolLogin = q;
  if (q.length < 2) {
    results.classList.add("hidden");
    results.innerHTML = "";
    return;
  }
  try {
    const payload = await request(`/api/schools/search?q=${encodeURIComponent(q)}`);
    renderSchoolResults(payload.data || []);
  } catch {
    results.classList.add("hidden");
    results.innerHTML = "";
  }
}

function renderSchoolResults(items) {
  const results = $("schoolResults");
  results.innerHTML = "";
  if (!items.length) {
    results.classList.add("hidden");
    return;
  }
  for (const school of items.slice(0, 8)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "school-result";
    button.innerHTML = `<strong>${escapeHtml(school.displayName || school.loginName || "")}</strong><span>${escapeHtml(school.address || school.server || "")}</span>`;
    button.addEventListener("click", () => {
      $("schoolInput").value = school.displayName || school.loginName || "";
      $("serverInput").value = school.server || $("serverInput").value;
      state.selectedSchoolLogin = school.loginName || $("schoolInput").value;
      state.selectedServer = school.server || $("serverInput").value;
      results.classList.add("hidden");
      results.innerHTML = "";
    });
    results.appendChild(button);
  }
  results.classList.remove("hidden");
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
  $("portraitBtn").classList.toggle("active", state.weekLayout !== "landscape");
  $("landscapeBtn").classList.toggle("active", state.weekLayout === "landscape");
  document.querySelectorAll(".lang-button").forEach((button) => button.classList.toggle("active", button.dataset.lang === state.lang));
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
    grid.className = state.weekLayout === "landscape" ? "week-grid" : "content-list";
    for (let i = 0; i < 5; i += 1) {
      const day = addDays(state.weekStart, i);
      const col = document.createElement("div");
      col.className = state.weekLayout === "landscape" ? "week-column" : "content-list";
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
  if (state.unsupported.has(state.tab)) {
    content.appendChild(emptyNode("Von dieser Schule nicht unterstuetzt"));
    return;
  }
  if (state.tab === "teachers") {
    renderTeachers(content);
    return;
  }
  if (!state.rows.length) {
    content.appendChild(emptyNode("Keine Daten"));
    return;
  }
  for (const row of state.rows) content.appendChild(rowNode(row));
}

function renderTeachers(content) {
  const picker = document.createElement("section");
  picker.className = "row-card teacher-picker";
  const options = state.rows
    .filter((teacher) => teacher.shortName || teacher.name)
    .map((teacher) => {
      const short = teacher.shortName || teacher.name || "";
      const label = teacher.fullName && teacher.fullName !== short ? `${teacher.fullName} (${short})` : short;
      return `<option value="${escapeHtml(short)}"${short === state.selectedTeacher ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
  picker.innerHTML = `
    <div class="row-title">Lehrer waehlen</div>
    <div class="teacher-actions">
      <select id="teacherSelect">${options}</select>
      <button id="teacherShowBtn" class="frame-button" type="button">Stunden anzeigen</button>
      <button id="teacherResetBtn" class="frame-button" type="button">Zuruecksetzen</button>
    </div>
  `;
  content.appendChild(picker);
  const select = picker.querySelector("#teacherSelect");
  if (!state.selectedTeacher && select?.value) state.selectedTeacher = select.value;
  select?.addEventListener("change", () => {
    state.selectedTeacher = select.value;
  });
  picker.querySelector("#teacherShowBtn")?.addEventListener("click", () => loadTeacherLessons());
  picker.querySelector("#teacherResetBtn")?.addEventListener("click", () => {
    state.selectedTeacher = "";
    state.teacherLessons = [];
    renderTeachersOnly();
  });

  if (state.teacherLessons.length) {
    const title = document.createElement("div");
    title.className = "week-heading";
    title.textContent = `Unterricht von ${state.selectedTeacher}`;
    content.appendChild(title);
    state.teacherLessons.forEach((lesson) => content.appendChild(lessonNode(lesson)));
  }

  if (!state.rows.length) content.appendChild(emptyNode("Keine Lehrer gefunden"));
  else state.rows.forEach((teacher) => content.appendChild(rowNode(teacher)));
}

function renderTeachersOnly() {
  if (state.tab === "teachers") render();
}

async function loadTeacherLessons() {
  if (!state.selectedTeacher) return;
  setNotice("Lade Lehrer-Stunden ...");
  const payload = await request(`/api/data/teachers/${encodeURIComponent(state.selectedTeacher)}/lessons`);
  state.teacherLessons = payload.status ? payload.data || [] : [];
  setNotice(payload.status ? "" : payload.message || "Lehrer-Stunden konnten nicht geladen werden");
  render();
}

function rowNode(row) {
  const node = document.createElement("article");
  node.className = "row-card";
  const title = row.subject || row.name || row.fullName || row.title || row.sender || formatDateDE(row.date) || "-";
  const parts = [
    formatDateDE(row.date || row.startDate),
    formatDateDE(row.endDate),
    formatDateDE(row.assigned),
    formatDateDE(row.due),
    row.teacher || row.shortName || row.originalTeacher,
    row.substituteTeacher,
    row.rooms || row.classroom,
    row.text,
  ].filter(Boolean);
  node.innerHTML = `<div class="row-title">${escapeHtml(title)}</div><div class="subline">${escapeHtml(parts.join(" · "))}</div>`;
  return node;
}

async function logout() {
  try {
    await request("/api/auth/logout", { method: "POST" });
  } catch {
    // Session cleanup is best-effort.
  }
  state.token = "";
  state.rows = [];
  state.lessons = [];
  state.teacherLessons = [];
  $("dashboard").classList.add("hidden");
  $("settingsPanel").classList.add("hidden");
  $("loginPanel").classList.remove("hidden");
  $("logoutBtn").classList.add("hidden");
  $("subtitle").textContent = "Stundenplan";
}

function showSettings(open) {
  $("settingsPanel").classList.toggle("hidden", !open);
  $("dashboard").classList.toggle("hidden", open || !state.token);
  $("loginPanel").classList.toggle("hidden", open || !!state.token);
  renderTabs();
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
  state.selectedSchoolLogin = "demo";
  state.selectedServer = "demo.local";
});
$("serverInput").addEventListener("input", () => {
  state.selectedServer = $("serverInput").value.trim();
});
$("schoolInput").addEventListener("input", () => {
  clearTimeout(state.schoolSearchTimer);
  state.selectedSchoolLogin = $("schoolInput").value.trim();
  state.schoolSearchTimer = setTimeout(() => searchSchools($("schoolInput").value), 300);
});
$("refreshBtn").addEventListener("click", () => reloadAll().catch((err) => setNotice(err.message)));
$("settingsBtn").addEventListener("click", () => showSettings(true));
$("closeSettingsBtn").addEventListener("click", () => showSettings(false));
$("logoutBtn").addEventListener("click", () => logout());
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
$("portraitBtn").addEventListener("click", () => {
  state.weekLayout = "portrait";
  renderTabs();
});
$("landscapeBtn").addEventListener("click", () => {
  state.weekLayout = "landscape";
  renderTabs();
});
document.querySelectorAll(".lang-button").forEach((button) => button.addEventListener("click", () => {
  state.lang = button.dataset.lang;
  renderTabs();
}));
tabs.forEach((button) => button.addEventListener("click", async () => {
  state.tab = button.dataset.tab;
  state.teacherLessons = state.tab === "teachers" ? state.teacherLessons : [];
  await loadRowsForTab(state.tab);
  render();
}));

loadOptions();
