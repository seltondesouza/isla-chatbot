import { api, changePasswordDialog, downloadText, el, fmtDate } from "./common.js";

const $ = (id) => document.getElementById(id);
let me = null;

const IMPORTS = [
  ["students", "Estudantes", "Cria contas e gera palavras-passe temporárias."],
  ["courses", "Unidades curriculares", ""],
  ["enrollments", "Inscrições em UCs", "Estudante ↔ UC."],
  ["timetable", "Horário", "Aulas semanais por UC e turma."],
  ["calendar", "Calendário académico", "Feriados (holiday), interrupções (break), exames, prazos."],
  ["charges", "Pagamentos", "Propinas e emolumentos por estudante e mês, com o motivo."],
  ["activities", "Atividades", "Atividades com candidatura pela app."],
];

// ── Tabs ─────────────────────────────────────────────────
const loaders = { overview: loadOverview, activities: loadActivities, documents: loadDocuments, users: loadUsers };
$("tabs").addEventListener("click", (e) => {
  const tab = e.target.dataset?.tab;
  if (!tab) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== tab));
  loaders[tab]?.();
});

const table = (node, headers, rows) =>
  node.replaceChildren(el("thead", {}, el("tr", {}, headers.map((h) => el("th", { text: h })))),
    el("tbody", {}, rows.length ? rows : el("tr", {}, el("td", { colspan: headers.length, class: "muted", text: "Sem dados." }))));

// ── Overview ─────────────────────────────────────────────
const STAT_LABELS = {
  students: "Estudantes", courses: "UCs", enrollments: "Inscrições", timetable_slots: "Aulas no horário",
  calendar_events: "Eventos do calendário", charges: "Pagamentos", activities: "Atividades",
  applications: "Candidaturas", documents: "Documentos indexados",
};

async function loadOverview() {
  const s = await api("/api/admin/status");
  $("stats").replaceChildren(...Object.entries(s.counts).map(([k, v]) =>
    el("div", { class: "card stat" }, el("div", { class: "n", text: v }), el("div", { class: "l", text: STAT_LABELS[k] || k }))));
  const c = s.crawl;
  $("crawl-info").textContent = c.running ? "A atualizar agora…"
    : c.last_run ? `Última atualização: ${new Date(c.last_run).toLocaleString("pt-PT")} — ${c.last_stats}. Automática a cada ${c.interval_hours} h${c.enabled ? "" : " (desligada)"}.`
    : "Ainda não foi feita nenhuma atualização.";
  $("crawl-now").disabled = c.running;
  $("llm-info").textContent = s.llm_enabled
    ? `Ligado: ${s.llm_model} (OpenRouter).`
    : "Desligado — falta OPENROUTER_API_KEY no .env. O chat funciona em modo básico (respostas automáticas sem IA).";
  if (s.temporary_database) {
    $("llm-info").textContent += " ⚠️ Base de dados temporária: liga uma base Postgres (Vercel → Storage) ou os dados perdem-se.";
  }
  if (c.running) setTimeout(loadOverview, 5000);
}

$("crawl-now").addEventListener("click", async () => {
  const btn = $("crawl-now");
  btn.disabled = true;
  $("crawl-info").textContent = "A atualizar… (pode demorar alguns minutos)";
  try { await api("/api/admin/crawl", { method: "POST" }); }
  catch (ex) { $("crawl-info").textContent = `Erro: ${ex.message}`; }
  btn.disabled = false;
  loadOverview();
});

$("demo-load").addEventListener("click", async () => {
  const btn = $("demo-load");
  btn.disabled = true;
  try {
    const r = await api("/api/admin/demo-data", { method: "POST" });
    const errors = Object.values(r.summary).flatMap((x) => x.errors);
    $("demo-result").replaceChildren(
      el("p", { text: errors.length ? `Carregado com ${errors.length} erro(s): ${errors.slice(0, 3).join("; ")}` : "Dados de demonstração carregados." }),
      r.new_credentials.length
        ? el("pre", { class: "secret", text: r.new_credentials.map((c) => `${c.student_number}  ${c.email}  ${c.temporary_password}`).join("\n") })
        : el("p", { class: "muted", text: "Os estudantes de demonstração já existiam (usa \"Repor palavra-passe\" em Utilizadores se precisares)." }));
    loadOverview();
  } catch (ex) { $("demo-result").textContent = `Erro: ${ex.message}`; }
  btn.disabled = false;
});

// ── Imports ──────────────────────────────────────────────
function buildImports() {
  $("imports").replaceChildren(...IMPORTS.map(([kind, title, hint]) => {
    const file = el("input", { type: "file", accept: ".csv,text/csv" });
    const result = el("div", { class: "import-result" });
    const btn = el("button", { class: "btn primary", text: "Importar", onclick: async () => {
      if (!file.files[0]) { result.textContent = "Escolhe um ficheiro."; return; }
      const form = new FormData();
      form.append("file", file.files[0]);
      btn.disabled = true;
      result.textContent = "A importar…";
      try {
        const r = await api(`/api/admin/import/${kind}`, { method: "POST", form });
        result.replaceChildren(
          el("b", { text: `${r.ok} linha(s) importadas` }), r.errors.length ? ` · ${r.errors.length} erro(s)` : "",
          r.errors.length ? el("ul", {}, r.errors.slice(0, 50).map((e) => el("li", { text: e }))) : null,
          r.new_credentials.length ? el("div", {},
            `${r.new_credentials.length} conta(s) nova(s). `,
            el("button", { class: "btn", text: "Descarregar palavras-passe (CSV)", onclick: async () => {
              const csv = await api("/api/admin/credentials.csv", { method: "POST", body: r.new_credentials });
              downloadText("credenciais.csv", csv);
            } }),
            el("span", { class: "muted small", text: " Guarda e entrega aos estudantes; não volta a ser mostrado." })) : null);
        file.value = "";
      } catch (ex) { result.textContent = `Erro: ${ex.message}`; }
      btn.disabled = false;
    } });
    return el("div", { class: "import-row" },
      el("div", {}, el("b", { text: title }), hint ? el("div", { class: "muted small", text: hint }) : null),
      file,
      el("a", { class: "btn link", href: `/api/admin/templates/${kind}.csv`, text: "Modelo CSV" }),
      btn, result);
  }));
}

// ── Activities ───────────────────────────────────────────
const actForm = $("act-form");

function resetActForm() {
  actForm.reset();
  actForm.id.value = "";
  $("act-form-title").textContent = "Nova atividade";
  $("act-error").textContent = "";
}
$("act-reset").addEventListener("click", resetActForm);

actForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = actForm;
  const body = {
    title: f.title.value, summary: f.summary.value || null, starts_on: f.starts_on.value || null,
    ends_on: f.ends_on.value || null, apply_deadline: f.apply_deadline.value || null,
    how_to_apply: f.how_to_apply.value || null, url: f.url.value || null,
    requires_application: f.requires_application.checked, is_published: f.is_published.checked,
    capacity: f.capacity.value ? Number(f.capacity.value) : null,
  };
  try {
    if (f.id.value) await api(`/api/admin/activities/${f.id.value}`, { method: "PUT", body });
    else await api("/api/admin/activities", { method: "POST", body });
    resetActForm();
    loadActivities();
  } catch (ex) { $("act-error").textContent = ex.message; }
});

function editActivity(a) {
  const f = actForm;
  f.id.value = a.id;
  for (const k of ["title", "summary", "starts_on", "ends_on", "apply_deadline", "how_to_apply", "url", "capacity"]) f[k].value = a[k] ?? "";
  f.requires_application.checked = a.requires_application;
  f.is_published.checked = a.is_published;
  $("act-form-title").textContent = `Editar: ${a.title}`;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadActivities() {
  const acts = await api("/api/admin/activities");
  table($("act-table"), ["Atividade", "Datas", "Origem", "Candidaturas", ""], acts.map((a) => el("tr", {},
    el("td", {}, el("b", { text: a.title }), a.is_published ? null : el("span", { class: "pill warn", text: " oculta" }),
      a.url ? el("div", {}, el("a", { href: a.url, target: "_blank", rel: "noopener noreferrer", class: "small", text: "link ↗" })) : null),
    el("td", { class: "small", text: [a.starts_on && fmtDate(a.starts_on), a.apply_deadline && `prazo ${fmtDate(a.apply_deadline)}`].filter(Boolean).join(" · ") || "—" }),
    el("td", {}, el("span", { class: "pill", text: a.source === "web" ? "site" : "manual" })),
    el("td", {}, a.requires_application
      ? el("button", { class: "btn link", text: `${a.applications}${a.capacity ? "/" + a.capacity : ""} ver`, onclick: () => loadApplications(a) })
      : el("span", { class: "muted small", text: "externa" })),
    el("td", {},
      el("button", { class: "btn link", text: "Editar", onclick: () => editActivity(a) }),
      el("button", { class: "btn link danger", text: "Apagar", onclick: async () => {
        if (!confirm(`Apagar "${a.title}" e as candidaturas?`)) return;
        await api(`/api/admin/activities/${a.id}`, { method: "DELETE" });
        loadActivities();
      } })))));
}

async function loadApplications(a) {
  const apps = await api(`/api/admin/activities/${a.id}/applications`);
  $("apps-card").classList.remove("hidden");
  $("apps-title").textContent = `Candidaturas — ${a.title}`;
  table($("apps-table"), ["Estudante", "Nota", "Data", "Estado"], apps.map((p) => {
    const sel = el("select", {}, ["submitted", "accepted", "rejected", "cancelled"].map((s) =>
      el("option", { value: s, selected: s === p.status, text: { submitted: "submetida", accepted: "aceite", rejected: "recusada", cancelled: "cancelada" }[s] })));
    sel.addEventListener("change", () => api(`/api/admin/applications/${p.id}`, { method: "PATCH", body: { status: sel.value } }));
    return el("tr", {},
      el("td", {}, p.student.name, el("div", { class: "muted small", text: `${p.student.number || ""} ${p.student.email}` })),
      el("td", { class: "small", text: p.note || "" }),
      el("td", { class: "small", text: fmtDate(p.created_at) }),
      el("td", {}, sel));
  }));
  $("apps-card").scrollIntoView({ behavior: "smooth" });
}

// ── Documents ────────────────────────────────────────────
let docTimer;
async function loadDocuments() {
  const q = encodeURIComponent($("doc-q").value);
  const docs = await api(`/api/admin/documents?q=${q}&kind=${$("doc-kind").value}`);
  table($("doc-table"), ["Documento", "Tipo", "UC", "Tamanho", "Atualizado", ""], docs.map((d) => el("tr", {},
    el("td", {}, d.url && d.url.startsWith("http") ? el("a", { href: d.url, target: "_blank", rel: "noopener noreferrer", text: d.title }) : d.title),
    el("td", {}, el("span", { class: "pill", text: d.kind })),
    el("td", { text: d.course || "geral" }),
    el("td", { class: "small", text: `${Math.round(d.chars / 1000)}k car.` }),
    el("td", { class: "small", text: fmtDate(d.updated_at) }),
    el("td", {}, el("button", { class: "btn link danger", text: "Remover", onclick: async () => {
      if (!confirm(`Remover "${d.title}" da pesquisa?`)) return;
      await api(`/api/admin/documents/${d.id}`, { method: "DELETE" });
      loadDocuments();
    } })))));
}
$("doc-q").addEventListener("input", () => { clearTimeout(docTimer); docTimer = setTimeout(loadDocuments, 300); });
$("doc-kind").addEventListener("change", loadDocuments);
$("doc-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("doc-error").textContent = "";
  try {
    await api("/api/admin/documents", { method: "POST", form: new FormData(e.target) });
    e.target.reset();
    loadDocuments();
  } catch (ex) { $("doc-error").textContent = ex.message; }
  btn.disabled = false;
});

// ── Users ────────────────────────────────────────────────
let userTimer;
function showSecret(title, email, password) {
  const modal = el("div", { class: "modal" }, el("div", { class: "card" },
    el("h2", { text: title }),
    el("p", { class: "muted small", text: "Entrega estes dados à pessoa. Será pedida uma nova palavra-passe no primeiro acesso." }),
    el("pre", { class: "secret", text: `${email}\n${password}` }),
    el("div", { class: "row" }, el("button", { class: "btn primary", text: "Fechar", onclick: () => modal.remove() }))));
  document.body.append(modal);
}

async function loadUsers() {
  const users = await api(`/api/admin/users?q=${encodeURIComponent($("user-q").value)}`);
  const roleName = { student: "estudante", staff: "secretaria", admin: "admin" };
  table($("user-table"), ["Nome", "Número", "Curso / turma", "Papel", "Estado", ""], users.map((u) => el("tr", {},
    el("td", {}, u.name, el("div", { class: "muted small", text: u.email })),
    el("td", { text: u.student_number || "—" }),
    el("td", { class: "small", text: [u.program, u.year && `${u.year}.º ano`, u.class_group].filter(Boolean).join(" · ") || "—" }),
    el("td", {}, el("span", { class: "pill", text: roleName[u.role] })),
    el("td", {}, el("span", { class: `pill ${u.is_active ? "ok" : "bad"}`, text: u.is_active ? "ativa" : "inativa" })),
    el("td", {},
      el("button", { class: "btn link", text: "Repor palavra-passe", onclick: async () => {
        if (!confirm(`Gerar nova palavra-passe temporária para ${u.name}?`)) return;
        try { const r = await api(`/api/admin/users/${u.id}/reset-password`, { method: "POST" }); showSecret("Nova palavra-passe", r.email, r.temporary_password); }
        catch (ex) { alert(ex.message); }
      } }),
      u.id === me.id ? null : el("button", { class: "btn link", text: u.is_active ? "Desativar" : "Ativar", onclick: async () => {
        try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: { is_active: !u.is_active } }); loadUsers(); }
        catch (ex) { alert(ex.message); }
      } })))));
}
$("user-q").addEventListener("input", () => { clearTimeout(userTimer); userTimer = setTimeout(loadUsers, 300); });
$("staff-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    const r = await api("/api/admin/staff", { method: "POST", body: { name: f.name.value, email: f.email.value, role: f.role.value } });
    f.reset();
    showSecret("Conta criada", r.email, r.temporary_password);
    loadUsers();
  } catch (ex) { $("staff-error").textContent = ex.message; }
});

// ── Init ─────────────────────────────────────────────────
async function init() {
  me = await api("/api/auth/me");
  if (me.role === "student") { location.href = "/chat"; return; }
  $("who").textContent = me.name;
  if (me.role === "admin") { $("staff-card").classList.remove("hidden"); $("demo-card").classList.remove("hidden"); }
  if (me.must_change_password) await changePasswordDialog({ forced: true });
  $("logout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/"; });
  $("change-pw").addEventListener("click", () => changePasswordDialog());
  buildImports();
  loadOverview();
}

init();
