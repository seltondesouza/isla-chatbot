import { api, changePasswordDialog, el, renderMarkdown } from "./common.js";

const $ = (id) => document.getElementById(id);
const app = $("app");
const state = { user: null, convId: null, busy: false, lastQuestion: "" };
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const svg = (paths) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.innerHTML = paths;
  return s;
};
const ICONS = {
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  euro: '<path d="M18 7a7 7 0 1 0 0 10M4 10h10M4 14h10"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  book: '<path d="M4 4h10a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z"/><path d="M8 20a4 4 0 0 1 0-8h10"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  retry: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
};

const SUGGESTIONS = [
  { text: "Que aulas tenho hoje?", icon: "calendar" },
  { text: "O que tenho de pagar este mês e porquê?", icon: "euro" },
  { text: "Quais são as próximas atividades da escola?", icon: "spark" },
  { text: "Como me candidato a uma bolsa de estudo?", icon: "book" },
];
const STATUSES = ["A pensar…", "A consultar os teus dados…", "A pesquisar no site da ISLA…", "A preparar a resposta…"];

// ── Rendering ────────────────────────────────────────────
const thread = $("thread"), inner = $("thread-inner");

function atBottom() { return thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80; }
function scrollDown(force = false) { if (force || atBottom()) thread.scrollTop = thread.scrollHeight; }

function botAvatar() {
  return el("div", { class: "bot-avatar" }, el("img", { src: "/static/logo.svg", alt: "" }));
}

function userTurn(text) {
  return el("div", { class: "turn user" }, el("div", { class: "user-bubble", text }));
}

function sourcesNode(sources) {
  const links = (sources || []).filter((s) => s.url && /^https?:/.test(s.url));
  if (!links.length) return null;
  return el("div", { class: "sources" }, links.map((s) => {
    let host = s.url;
    try { host = new URL(s.url).hostname.replace(/^www\./, ""); } catch { /* keep url */ }
    return el("a", { class: "source", href: s.url, target: "_blank", rel: "noopener noreferrer", title: s.url },
      svg(ICONS.link), el("span", { text: s.title || host }));
  }));
}

function actionsNode(answer, question) {
  const copyBtn = el("button", { class: "icon-btn", title: "Copiar", "aria-label": "Copiar resposta" }, svg(ICONS.copy));
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(answer);
      copyBtn.replaceChildren(svg(ICONS.check));
      copyBtn.classList.add("done");
      setTimeout(() => { copyBtn.replaceChildren(svg(ICONS.copy)); copyBtn.classList.remove("done"); }, 1500);
    } catch { /* clipboard blocked */ }
  });
  const retryBtn = question
    ? el("button", { class: "icon-btn", title: "Tentar de novo", "aria-label": "Tentar de novo", onclick: () => send(question) }, svg(ICONS.retry))
    : null;
  return el("div", { class: "actions" }, copyBtn, retryBtn);
}

function botTurn(answer, sources, question, { animate = false } = {}) {
  const md = el("div", { class: "md" });
  const body = el("div", { class: "bot-body" }, md);
  const turn = el("div", { class: "turn bot" }, botAvatar(), body);
  const finish = () => {
    md.innerHTML = renderMarkdown(answer);
    const src = sourcesNode(sources);
    if (src) body.append(src);
    body.append(actionsNode(answer, question));
  };
  if (!animate || reducedMotion) { finish(); return turn; }

  // Reveal the answer progressively, word by word (Markdown re-rendered as it grows).
  const words = answer.split(/(\s+)/);
  let i = 0, last = 0;
  const step = (t) => {
    const perFrame = Math.max(2, Math.round(words.length / 90)); // ~1.5 s for any length
    if (t - last > 16) {
      i = Math.min(words.length, i + perFrame);
      last = t;
      md.innerHTML = renderMarkdown(words.slice(0, i).join(""));
      let tail = md.lastElementChild; // put the typing dot inside the last list item, not under the list
      while (tail && /^(UL|OL)$/.test(tail.tagName) && tail.lastElementChild) tail = tail.lastElementChild;
      tail?.classList.add("caret");
      scrollDown();
    }
    if (i < words.length) requestAnimationFrame(step);
    else { finish(); scrollDown(); }
  };
  requestAnimationFrame(step);
  return turn;
}

function thinkingTurn() {
  const status = el("div", { class: "status", text: STATUSES[0] });
  const turn = el("div", { class: "turn bot thinking" }, botAvatar(),
    el("div", { class: "bot-body" }, status, el("div", { class: "shimmer" }, el("span"), el("span"), el("span"))));
  let n = 0;
  turn._timer = setInterval(() => {
    n = Math.min(n + 1, STATUSES.length - 1);
    status.textContent = STATUSES[n];
  }, 2200);
  return turn;
}

function showWelcome() {
  const first = (state.user?.name || "").split(" ")[0];
  inner.replaceChildren(el("div", { class: "hello" },
    el("h1", {}, el("span", { class: "grad-text", text: `Olá${first ? ", " + first : ""}` })),
    el("h2", { text: "Como posso ajudar hoje?" }),
    el("div", { class: "cards" }, SUGGESTIONS.map((s, i) => {
      const card = el("button", { class: "card-sug", onclick: () => send(s.text) },
        el("span", { text: s.text }), el("span", { class: "ic" }, svg(ICONS[s.icon])));
      card.style.animationDelay = `${0.25 + i * 0.07}s`;
      return card;
    }))));
}

// ── Sending ──────────────────────────────────────────────
const input = $("input"), sendBtn = $("send");

function syncSend() { sendBtn.disabled = state.busy || !input.value.trim(); }

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;
  state.busy = true;
  state.lastQuestion = text;
  syncSend();
  if (inner.querySelector(".hello")) inner.replaceChildren();
  inner.append(userTurn(text));
  const thinking = thinkingTurn();
  inner.append(thinking);
  scrollDown(true);
  try {
    const res = await api("/api/chat", { method: "POST", body: { message: text, conversation_id: state.convId } });
    clearInterval(thinking._timer);
    thinking.replaceWith(botTurn(res.answer, res.sources, text, { animate: true }));
    const isNew = !state.convId;
    state.convId = res.conversation_id;
    if (isNew) loadConversations();
  } catch (ex) {
    clearInterval(thinking._timer);
    const t = botTurn("", [], text);
    t.querySelector(".md").replaceChildren(el("p", { class: "error-text", text: ex.message }));
    thinking.replaceWith(t);
  } finally {
    state.busy = false;
    syncSend();
    scrollDown();
    input.focus();
  }
}

// ── Conversations ────────────────────────────────────────
async function loadConversations() {
  const convs = await api("/api/conversations");
  $("conv-list").replaceChildren(...convs.map((c, i) => {
    const row = el("div", { class: `conv ${c.id === state.convId ? "active" : ""}` },
      el("button", { class: "open", title: c.title, text: c.title, onclick: () => openConversation(c.id) }),
      el("button", { class: "icon-btn del", title: "Apagar", "aria-label": "Apagar conversa",
        onclick: async () => {
          if (!confirm("Apagar esta conversa?")) return;
          await api(`/api/conversations/${c.id}`, { method: "DELETE" });
          if (state.convId === c.id) newConversation();
          else loadConversations();
        } }, svg(ICONS.trash)));
    row.style.animationDelay = `${Math.min(i, 12) * 0.02}s`;
    return row;
  }));
}

async function openConversation(id) {
  const conv = await api(`/api/conversations/${id}`);
  state.convId = id;
  let lastQ = "";
  inner.replaceChildren(...conv.messages.map((m) => {
    if (m.role === "user") { lastQ = m.content; return userTurn(m.content); }
    return botTurn(m.content, m.sources, lastQ);
  }));
  closeDrawer();
  loadConversations();
  requestAnimationFrame(() => scrollDown(true));
}

function newConversation() {
  state.convId = null;
  showWelcome();
  loadConversations();
  closeDrawer();
  input.focus();
}

// ── Rail, theme, dictation ───────────────────────────────
function closeDrawer() { app.classList.remove("drawer"); }

function setupRail() {
  let collapsed = false;
  try { collapsed = localStorage.getItem("isla-rail") === "collapsed"; } catch { /* ignore */ }
  app.classList.toggle("collapsed", collapsed);
  $("rail-toggle").addEventListener("click", () => {
    const c = app.classList.toggle("collapsed");
    try { localStorage.setItem("isla-rail", c ? "collapsed" : "open"); } catch { /* ignore */ }
  });
  $("menu-mobile").addEventListener("click", () => app.classList.add("drawer"));
  $("scrim").addEventListener("click", closeDrawer);
}

function setupTheme() {
  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("isla-theme", next); } catch { /* ignore */ }
  });
}

function setupDictation() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  const mic = $("mic");
  mic.classList.remove("hidden");
  let rec = null;
  mic.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    rec = new SR();
    rec.lang = "pt-PT";
    rec.interimResults = true;
    const base = input.value ? input.value.trimEnd() + " " : "";
    rec.onresult = (e) => {
      input.value = base + Array.from(e.results).map((r) => r[0].transcript).join("");
      autosize();
      syncSend();
    };
    rec.onend = () => { mic.classList.remove("listening"); rec = null; input.focus(); };
    rec.onerror = () => rec?.stop();
    mic.classList.add("listening");
    rec.start();
  });
}

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

// ── Init ─────────────────────────────────────────────────
async function init() {
  setupRail();
  setupTheme();
  setupDictation();

  state.user = await api("/api/auth/me");
  const u = state.user;
  $("avatar").textContent = u.name.split(" ").filter(Boolean).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
  $("avatar").title = [u.name, u.student_number, u.class_group && `turma ${u.class_group}`].filter(Boolean).join(" · ");
  if (u.role !== "student") $("admin-link").classList.remove("hidden");
  if (u.must_change_password) await changePasswordDialog({ forced: true });

  fetch("/api/health").then((r) => r.json()).then((h) => {
    $("model-chip").textContent = h.llm_enabled ? "IA" : "Modo básico";
    if (!h.llm_enabled) $("mode-note").textContent = "Modo básico (sem IA): respondo a horário, pagamentos, atividades e pesquisa nos documentos.";
  }).catch(() => {});

  showWelcome();
  loadConversations();

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = input.value;
    input.value = "";
    autosize();
    send(v);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  input.addEventListener("input", () => { autosize(); syncSend(); });
  $("new-chat").addEventListener("click", newConversation);
  $("logout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }); location.href = "/"; });
  $("change-pw").addEventListener("click", () => changePasswordDialog());
  thread.addEventListener("scroll", () => $("jump").classList.toggle("hidden", atBottom()));
  $("jump").addEventListener("click", () => scrollDown(true));
  input.focus();
}

init();
