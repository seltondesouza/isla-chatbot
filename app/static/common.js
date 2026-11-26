// Shared helpers: API calls, safe Markdown rendering, small DOM utilities.

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    location.href = "/";
    throw new Error("Sessão terminada.");
  }
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data && data.detail;
    const msg = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail || `Erro ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  // s is already escaped
  return s
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/** Minimal Markdown: paragraphs, bullet/numbered lists, bold, italics, links. HTML is escaped first. */
export function renderMarkdown(text) {
  const lines = escapeHtml(text || "").split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (bullet || num) {
      flushPara();
      const want = bullet ? "ul" : "ol";
      if (list !== want) { closeList(); html += `<${want}>`; list = want; }
      html += `<li>${inline((bullet || num)[1])}</li>`;
    } else if (heading) {
      flushPara(); closeList();
      html += `<p><strong>${inline(heading[1])}</strong></p>`;
    } else if (!line.trim()) {
      flushPara(); closeList();
    } else {
      closeList();
      para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return d.toLocaleDateString("pt-PT", { day: "numeric", month: "short", year: "numeric" });
}

export function downloadText(filename, text, type = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Change-password dialog. Resolves when done. */
export function changePasswordDialog({ forced = false } = {}) {
  return new Promise((resolve) => {
    const err = el("div", { class: "error" });
    const cur = el("input", { type: "password", autocomplete: "current-password", required: true });
    const n1 = el("input", { type: "password", autocomplete: "new-password", required: true, minlength: "10" });
    const n2 = el("input", { type: "password", autocomplete: "new-password", required: true });
    const close = () => { modal.remove(); resolve(); };
    const form = el("form", { class: "card" },
      el("h2", { text: forced ? "Define a tua palavra-passe" : "Mudar palavra-passe" }),
      forced ? el("p", { class: "muted small", text: "Estás a usar uma palavra-passe temporária. Escolhe uma nova (mín. 10 caracteres)." }) : null,
      el("label", { text: "Palavra-passe atual" }), cur,
      el("label", { text: "Nova palavra-passe" }), n1,
      el("label", { text: "Repetir nova palavra-passe" }), n2,
      err,
      el("div", { class: "row" },
        forced ? null : el("button", { type: "button", class: "btn", onclick: close, text: "Cancelar" }),
        el("button", { type: "submit", class: "btn primary", text: "Guardar" })));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (n1.value !== n2.value) { err.textContent = "As palavras-passe não coincidem."; return; }
      try {
        await api("/api/auth/change-password", { method: "POST", body: { current_password: cur.value, new_password: n1.value } });
        close();
      } catch (ex) { err.textContent = ex.message; }
    });
    const modal = el("div", { class: "modal" }, form);
    document.body.append(modal);
    cur.focus();
  });
}
