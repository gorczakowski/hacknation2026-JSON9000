// JSON9000 UI: upload a PDF, show its pages with the place every value came from, the JSON, and the config.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const TYPE_NAMES = {
  odwolanie: "Odwołanie do KIO",
  pelnomocnictwo: "Pełnomocnictwo",
  przelew: "Potwierdzenie przelewu",
  orzeczenie_kio: "Orzeczenie KIO",
};
const PROV = { rules: "reguła", model: "model", derived: "wyliczone", empty: "puste" };
const GROUP_TAG = { default: "domyślny", added: "dodany", extra: "z linii poleceń" };

const S = { state: null, jobId: null, job: null, view: null, active: -1, showEmpty: false, timer: null, seenOcr: false, t0: 0 };

// --- api ----------------------------------------------------------------------------------------------------
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("Content-Type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const err = new Error((body && body.error) || (body && body.problems && body.problems.join("\n")) || `Błąd ${r.status}`);
    err.body = body;
    throw err;
  }
  return body;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => (t.hidden = true), 6000);
}

const llmParam = () => ($("#llm").checked ? "1" : "0");
const typeName = (n) => TYPE_NAMES[n] || n;
const ENGINE = { mineru: "MinerU", tesseract: "Tesseract" };
const engine = (e) => ENGINE[e] || e || "OCR";
const num = (x) => String(Math.round(x * 10) / 10).replace(".", ",");

// --- start -------------------------------------------------------------------------------------------------
async function init() {
  S.state = await api("/api/state");
  renderModelSwitch();
  renderSamples();
  renderTypes();
  renderRecent();
  const L = S.state.limits;
  $("#limits").textContent = `Do ${L.max_mb} MB i ${L.max_pages} stron. Pliki zostają tylko w pamięci tego serwera.`;

  document.querySelectorAll("[data-pick], #pick").forEach((b) => b.addEventListener("click", () => $("#file").click()));
  $("#file").addEventListener("change", (e) => e.target.files[0] && upload(e.target.files[0]));
  $("#show-empty").addEventListener("change", (e) => { S.showEmpty = e.target.checked; renderFields(); });
  $("#recent").addEventListener("change", (e) => e.target.value && openJob(e.target.value));
  $("#llm").addEventListener("change", markDirty);
  $("#rerun").addEventListener("click", rerun);
  $("#add-type").addEventListener("click", () => $("#type-file").click());
  $("#type-file").addEventListener("change", (e) => e.target.files[0] && addType(e.target.files[0]));
  $("#json-copy").addEventListener("click", copyJson);
  $("#samples-filter").addEventListener("input", renderSamples);
  setupTabs();
  setupDrop();
  const tab = new URLSearchParams(location.search).get("tab");  // ?tab=json|types opens that tab
  if (tab) $(`#t-${tab}`)?.click();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && S.active >= 0) select(-1);
  });
  const [hid, hfi] = location.hash.slice(1).split(":");  // #<job>[:<field>] survives a refresh
  if (/^\w+$/.test(hid || "")) { S.pendingField = hfi ? +hfi : -1; openJob(hid); }
}

// settings changed after the shown result: the result no longer matches them until the document is re-run
function markDirty() {
  if (!S.view) return;
  S.dirty = true;
  updateRerun();
}

function updateRerun() {
  const b = $("#rerun");
  b.disabled = !S.view || S.job?.status === "running" || S.job?.status === "queued";
  b.classList.toggle("btn-due", !!S.dirty && !b.disabled);
  b.title = S.dirty ? "Ustawienia się zmieniły. Przetwórz dokument ponownie, żeby je zastosować." : "Przetwórz bieżący dokument jeszcze raz";
}

function setHash() {
  const h = S.jobId ? `#${S.jobId}${S.active >= 0 ? ":" + S.active : ""}` : "";
  if (location.hash !== h) history.replaceState(null, "", h || location.pathname);
}

function renderModelSwitch() {
  const m = S.state.model;
  const box = $("#llm");
  box.checked = !!m;
  box.disabled = !m;
  $("#llm-text").innerHTML = m ? `Model <b>${esc(m.split("/").pop())}</b>`
    : S.state.model_off === "no_key" ? "Model wyłączony: brak klucza API w .env, działają same reguły" : "Model wyłączony, działają same reguły";
}

function renderSamples() {
  const all = S.state.samples || [];
  $("#samples").hidden = !all.length;
  if (!all.length) return;
  const q = $("#samples-filter").value.trim().toLowerCase();
  const list = all.filter((s) => !q || s.toLowerCase().includes(q)).slice(0, 120);
  $("#samples-list").innerHTML = list.map((s) => {
    const i = s.lastIndexOf("/");
    return `<li><button type="button" data-sample="${esc(s)}"><span class="dir">${esc(s.slice(0, i + 1))}</span>${esc(s.slice(i + 1))}</button></li>`;
  }).join("") || `<li class="muted">Nic nie pasuje do „${esc(q)}”.</li>`;
  $("#samples-list").querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", () => openSample(b.dataset.sample)));
}

// Recent documents are remembered by this browser only: other people using the same server never see them.
const RECENT_KEY = "json9000.recent";
const loadRecent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch { return []; } };
const saveRecent = (r) => { try { localStorage.setItem(RECENT_KEY, JSON.stringify(r.slice(0, 12))); } catch { /* private mode */ } };

function rememberJob(v) {
  saveRecent([{ id: v.id, name: v.file, type: v.type }, ...loadRecent().filter((j) => j.id !== v.id)]);
}

function forgetJob(id) {
  saveRecent(loadRecent().filter((j) => j.id !== id));
}

function renderRecent() {
  const r = loadRecent();
  $("#recent-wrap").hidden = r.length < 1;
  const open = S.view && r.some((j) => j.id === S.jobId);
  $("#recent").innerHTML = `<option value="" ${open ? "" : "selected"} disabled>Ostatnie dokumenty</option>` +
    r.map((j) => `<option value="${esc(j.id)}" ${open && j.id === S.jobId ? "selected" : ""}>${esc(j.name)}${j.type ? ` (${esc(typeName(j.type))})` : ""}</option>`).join("");
}

// --- tabs, drag and drop -----------------------------------------------------------------------------------------
function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const show = (t) => {
    tabs.forEach((x) => {
      const on = x === t;
      x.setAttribute("aria-selected", on);
      x.tabIndex = on ? 0 : -1;
      $("#" + x.getAttribute("aria-controls")).hidden = !on;
    });
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => show(t));
    t.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); show(n); }
    });
  });
}

function setupDrop() {
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; depth++; $("#veil").hidden = false; document.body.classList.add("dragging"); });
  addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#veil").hidden = true; document.body.classList.remove("dragging"); } });
  addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    $("#veil").hidden = true;
    document.body.classList.remove("dragging");
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  });
}

// --- jobs ---------------------------------------------------------------------------------------------------
async function upload(file) {
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return toast("To nie jest plik PDF. Wybierz plik .pdf.");
  if (file.size > S.state.limits.max_mb * 1048576) return toast(`Plik ma ${num(file.size / 1048576)} MB. Limit to ${S.state.limits.max_mb} MB.`);
  try {
    const r = await api(`/api/upload?llm=${llmParam()}`, { method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/pdf" } });
    openJob(r.id, file.name);
  } catch (e) { toast(e.message); }
  $("#file").value = "";
}

async function openSample(path) {
  try {
    const r = await api(`/api/samples?llm=${llmParam()}`, { method: "POST", body: JSON.stringify({ path }), headers: { "Content-Type": "application/json" } });
    openJob(r.id, path);
  } catch (e) { toast(e.message); }
}

async function rerun() {
  if (!S.jobId || !S.view) return;
  try {
    await api(`/api/jobs/${S.jobId}/rerun?llm=${llmParam()}`, { method: "POST" });
    openJob(S.jobId, S.view.file, true);
  } catch (e) { toast(e.message); }
}

function openJob(id, name = "", keepPages = false) {
  clearTimeout(S.timer);
  if (id !== S.jobId) { S.view = null; S.active = -1; }
  S.jobId = id;
  S.seenOcr = false;
  S.t0 = Date.now();
  $("#busy-name").textContent = name;
  if (!keepPages) { $("#pages").innerHTML = ""; }
  setHash();
  poll();
}

async function poll() {
  const id = S.jobId;
  let j;
  try { j = await api(`/api/jobs/${id}`); } catch (e) {
    toast(e.message);
    forgetJob(id);
    S.jobId = null; S.view = null; S.job = null;
    setHash(); renderRecent(); updateRerun();
    $("#result").hidden = true; $("#fields-empty").hidden = false;
    return showEmpty();
  }
  if (id !== S.jobId) return;
  S.job = j;
  updateRerun();
  if (j.status === "done") {
    $("#busy").hidden = true;
    renderView(j.view);
    rememberJob(j.view);
    renderRecent();
    return;
  }
  if (j.status === "error") {
    $("#busy").hidden = true;
    toast(`Nie udało się przetworzyć dokumentu: ${j.error}`);
    return S.view ? renderView(S.view) : showEmpty();
  }
  renderBusy(j);
  S.timer = setTimeout(poll, 700);
}

function showEmpty() {
  $("#empty").hidden = false;
  $("#busy").hidden = true;
  $("#pages").innerHTML = "";
}

function renderBusy(j) {
  $("#empty").hidden = true;
  $("#busy").hidden = false;
  $("#pages").innerHTML = "";
  $("#busy-name").textContent = j.name;
  if (j.stage === "ocr") S.seenOcr = true;
  const steps = [["read", "Czytam tekst z PDF-a"]];
  if (S.seenOcr) steps.push(["ocr", `OCR skanu (${engine(S.state.ocr)}), ${j.pages} ${j.pages === 1 ? "strona" : "stron"}`]);
  steps.push([j.use_llm ? "model" : "extract", j.use_llm ? "Rozpoznaję typ, reguły odczytują pola, model uzupełnia braki" : "Rozpoznaję typ i odczytuję pola regułami"]);
  const order = steps.map((s) => s[0]);
  const now = j.stage === "queued" ? -1 : order.indexOf(j.stage);
  const secs = Math.round((Date.now() - S.t0) / 1000);
  $("#steps").innerHTML = steps.map(([k, label], i) =>
    `<li class="${i < now ? "done" : i === now ? "now" : ""}">${label}${i === now ? `<span class="t">${secs} s</span>` : ""}</li>`).join("");
  $("#busy-note").textContent = j.stage === "queued" ? "Czekam na poprzedni dokument. Serwer przetwarza jeden naraz."
    : S.seenOcr ? "OCR działa lokalnie na CPU i zwykle trwa 10 do 40 sekund na dokument." : "";
}

// --- result -------------------------------------------------------------------------------------------------
function renderView(v) {
  const sameDoc = S.view && S.view.id === v.id;
  S.view = v;
  S.dirty = false;
  if (!sameDoc) S.active = -1;
  else if (S.active >= v.fields.length) S.active = -1;
  $("#empty").hidden = true;
  $("#busy").hidden = true;
  $("#fields-empty").hidden = true;
  $("#result").hidden = false;
  renderSummary();
  renderScores();
  renderFields();
  renderPages();
  renderJson();
  updateRerun();
  if (S.pendingField !== undefined) { S.active = S.pendingField; delete S.pendingField; renderFields(); }
  if (S.active >= 0) select(S.active, { scroll: "page", force: true });
}

function renderSummary() {
  const v = S.view;
  $("#doc-file").textContent = v.file;
  $("#doc-type").textContent = v.type ? typeName(v.type) : "Nie rozpoznano typu";
  const src = v.source === "text" ? "Tekst z warstwy tekstowej PDF-a."
    : v.source === "ocr" ? `Skan: tekst z OCR (${engine(v.ocr_engine)}).`
    : v.source === "text+ocr" ? `Część stron to skany, odczytane przez OCR (${engine(v.ocr_engine)}).` : "Brak tekstu w dokumencie.";
  let how;
  if (!v.type) {
    how = v.tie ? `Typy ${v.tie.map(typeName).join(" i ")} mają tyle samo punktów, więc wynik to pusty JSON {}.`
      : "Żaden typ nie osiągnął progu punktów, więc wynik to pusty JSON {}, tak jak wymaga zadanie.";
  } else {
    const byModel = /model/i.test(v.type_decided_by || "");
    how = byModel ? "Reguły nie były pewne typu, więc rozstrzygnął model." : `Typ rozpoznany regułami z pliku ${v.type_file}.`;
  }
  const fieldsBy = v.fields_decided_by || "";
  const model = v.model ? (/model/.test(fieldsBy) ? (/model_first/.test(fieldsBy) ? " Na skanie pierwszeństwo ma model, reguły uzupełniają." : " Model uzupełnił pola, których reguły nie znalazły.") : /failed/.test(fieldsBy) ? " Model nie odpowiedział, zostały wyniki reguł." : "") : v.type ? " Bez modelu: tylko reguły." : "";
  const t = v.timing ? ` Odczyt ${num(v.timing.read_s)} s, ekstrakcja ${num(v.timing.process_s)} s.` : "";
  $("#doc-how").textContent = `${how}${model} ${src}${t}`;
  $("#doc-warn").hidden = !v.read_error;
  $("#doc-warn").textContent = v.read_error ? `Nie udało się odczytać części stron: ${v.read_error}` : "";
  $("#why").open = !v.type;
}

function renderScores() {
  const rows = [...S.view.classify].sort((a, b) => (b.chosen - a.chosen) || (b.score - a.score));
  const vals = rows.flatMap((r) => [r.score, r.threshold]);
  const lo = Math.min(0, ...vals), hi = Math.max(...vals) * 1.08 || 1;
  const pos = (x) => ((x - lo) / (hi - lo)) * 100;
  $("#scores").innerHTML = rows.map((r, i) => {
    const a = pos(Math.min(0, r.score)), b = pos(Math.max(0, r.score));
    return `<button type="button" class="score ${r.chosen ? "chosen" : ""}" data-i="${i}" aria-expanded="false" title="${esc(r.file)}">
        <span class="score-name">${esc(typeName(r.name))}</span>
        <span class="score-bar"><span class="score-fill ${r.score < 0 ? "neg" : ""}" style="left:${a}%;width:${b - a}%"></span>
          ${lo < 0 ? `<span class="score-zero" style="left:${pos(0)}%"></span>` : ""}
          <span class="score-tick" style="left:calc(${pos(r.threshold)}% - 1px)" title="próg ${r.threshold}"></span></span>
        <span class="score-num">${num(r.score)} / ${num(r.threshold)}</span>
      </button>
      <ul class="rules" hidden>${r.rules.map((x) => `<li class="${x.hit ? "hit" : ""}"><span class="w">${x.weight > 0 ? "+" : ""}${x.weight}</span><code>${esc(x.pattern)}${x.anywhere ? " (cały dokument)" : ""}</code></li>`).join("")}</ul>`;
  }).join("");
  $("#scores").querySelectorAll(".score").forEach((b) => b.addEventListener("click", () => {
    const ul = b.nextElementSibling;
    ul.hidden = !ul.hidden;
    b.setAttribute("aria-expanded", !ul.hidden);
  }));
}

function display(v) {
  if (v === null || v === undefined || v === "") return "brak";
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : x)).join(", ") : "brak pozycji";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function groupTitle(g) {
  return g.replace(/\[(\d+)\]/g, (_, n) => ` ${+n + 1}`).split(".").join(" › ");
}

function methodText(f) {
  const m = f.method || "";
  const lab = (s) => s.replace(/\\s[+*]?/g, " ").replace(/\\w[+*]?/g, "…").replace(/\(\?:|[()?^$\\]/g, "").replace(/\|/g, " / ").replace(/\s+/g, " ").trim();
  if (!m) return f.prov === "derived" ? "Wyliczona w kroku końcowym (derive) z innych pól dokumentu." : "";
  if (m.startsWith("label:")) return `Wartość po etykiecie „${esc(lab(m.slice(6)))}”.`;
  if (m.startsWith("names_after:")) return `Imiona i nazwiska po „${esc(lab(m.slice(12)) || "początku sekcji")}”.`;
  if (m.startsWith("nth:")) { const [, n, kind] = m.split(":"); return `${+n + 1}. ${kind === "account" ? "numer rachunku" : kind} w dokumencie.`; }
  if (m.startsWith("find:")) return `Pierwszy ${({ email: "adres e-mail", bzp: "numer BZP", ted: "numer TED", date: "data" })[m.slice(5)] || m.slice(5)} w zakresie reguły.`;
  if (m.startsWith("model:")) return m.includes("model_first") ? "Odpowiedź modelu (na skanach model ma pierwszeństwo, reguły uzupełniają)." : "Model uzupełnił pole, którego reguły nie znalazły.";
  return ({
    regex: "Wyrażenie regularne z konfiguracji.",
    place_date: "Linia „Miejscowość, dnia …”.",
    "place_date:date_only": "Linia z samą datą w nagłówku.",
    first_line: "Pierwsza linia sekcji.",
    const: "Stała wartość z konfiguracji.",
    passage_rules: "Słowa kluczowe w pierwszym zarzucie (reguły powod w YAML).",
    passage_date: "Data z fragmentu o zaskarżonej czynności.",
    passage_model: "Model wybrał kod 1–12 na podstawie fragmentu z pierwszym zarzutem.",
    model_document: "Kod z odpowiedzi modelu dla całego dokumentu.",
    signatories: "Podpisujący, wyliczeni w kroku signatories.",
  })[m] || esc(m);
}

function renderFields() {
  const v = S.view;
  if (!v) return;
  const counts = { rules: 0, model: 0, derived: 0, empty: 0 };
  v.fields.forEach((f) => counts[f.prov]++);
  const order = ["rules", "model", "derived", "empty"].filter((p) => counts[p]);
  const total = v.fields.length || 1;
  $("#legend").innerHTML = `<div class="share" aria-hidden="true">${order.map((p) =>
      `<span class="share-${p} prov-${p}" style="flex-grow:${counts[p]}"></span>`).join("")}</div>
    <ul class="share-labels">${order.map((p) => `<li><span class="${p === "empty" ? "" : `ink prov-${p}`}">${PROV[p]}</span> ${counts[p]}</li>`).join("")}</ul>`;
  $("#legend").setAttribute("aria-label", order.map((p) => `${PROV[p]}: ${counts[p]} z ${total}`).join(", "));
  $(".legend-row").hidden = !v.type;
  if (!v.type) {
    $("#fields").innerHTML = `<p class="muted">Brak pól: dokument spoza skonfigurowanych typów. Możesz włączyć kolejny typ w zakładce „Typy dokumentów”.</p>`;
    return;
  }
  let html = "", group = null;
  v.fields.forEach((f, i) => {
    if (f.prov === "empty" && !S.showEmpty && i !== S.active) return;
    if (f.group !== group) {
      if (group !== null) html += "</div>";
      group = f.group;
      html += `<div class="group">${group ? `<h2 class="group-title">${esc(groupTitle(group))}</h2>` : ""}`;
    }
    html += fieldHtml(f, i);
  });
  if (group !== null) html += "</div>";
  $("#fields").innerHTML = html || `<p class="muted">Wszystkie pola są puste.</p>`;
  $("#fields").querySelectorAll(".field").forEach((b) => {
    const i = +b.dataset.fi;
    b.addEventListener("click", () => select(S.active === i ? -1 : i, { scroll: "page" }));
    b.addEventListener("mouseenter", () => hot(i, true));
    b.addEventListener("mouseleave", () => hot(i, false));
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const all = [...$("#fields").querySelectorAll(".field")];
      const n = all[all.indexOf(b) + (e.key === "ArrowDown" ? 1 : -1)];
      if (n) n.focus();
    });
  });
}

function fieldHtml(f, i) {
  const on = i === S.active;
  let flag = "";
  if (f.check === true) flag = `<span class="f-flag ok">suma kontrolna poprawna</span>`;
  else if (f.check === false) flag = `<span class="f-flag warn">suma kontrolna się nie zgadza</span>`;
  // categories (osoba_prawna) and small codes/flags (1-12, 0/1) are not written in the document as such
  const category = (typeof f.value === "string" && /^[a-z_]+$/.test(f.value)) || (typeof f.value === "number" && Math.abs(f.value) < 100);
  if (f.prov === "model" && !f.boxes.length && !category && !Array.isArray(f.value)) flag += `<span class="f-flag warn">tej wartości nie ma w tekście dokumentu</span>`;
  else if (f.prov === "rules" && !f.boxes.length) flag += `<span class="f-flag">nie wskazano miejsca na stronie</span>`;
  const label = f.empty_list ? "brak pozycji" : f.shown ?? display(f.value);
  let more = "";
  if (on) {
    const pages = [...new Set(f.boxes.map((b) => b.p + 1))];
    const approx = f.boxes.some((b) => b.approx);
    more = `<div class="f-more">
      ${f.desc ? `<p><span class="k">Opis w szablonie:</span> ${esc(f.desc)}</p>` : ""}
      ${methodText(f) ? `<p><span class="k">Jak odczytano:</span> ${methodText(f)}${pages.length ? ` Strona ${pages.join(", ")}.` : ""}</p>` : ""}
      ${approx ? `<p class="k">OCR podał tu cały akapit, więc zaznaczona linia jest szacunkowa (przerywana kreska).</p>` : ""}
      ${f.context && f.prov !== "model" ? `<p class="f-ctx">…${esc(f.context)}…</p>` : ""}
      ${f.rule ? `<div class="f-rule"><span class="k">${f.prov === "empty" ? "Reguła, która nic nie znalazła" : "Reguła"} w ${esc(S.view.type_file)}:</span><pre>${esc(f.path.replace(/\[\d+\]/g, ""))}:\n${esc(f.rule.replace(/^/gm, "  "))}</pre></div>` : ""}
    </div>`;
  }
  return `<div class="fi prov-${f.prov} ${on ? "on" : ""}"><button type="button" class="field prov-${f.prov} ${on ? "on" : ""}" data-fi="${i}" aria-expanded="${on}">
      <span class="f-head"><span class="f-key">${esc(f.key)}</span><span class="f-prov">${PROV[f.prov]}</span></span>
      <span class="f-val">${f.prov === "empty" ? esc(label) : `<span class="ink prov-${f.prov}${f.boxes.some((b) => b.approx) ? " approx" : ""}${f.boxes.length ? "" : " nowhere"}">${esc(label)}</span>`}</span>${flag ? `<span class="f-flags">${flag}</span>` : ""}
    </button>${more}</div>`;
}

function renderPages() {
  const v = S.view;
  const el = $("#pages");
  const n = v.pages.length;
  el.innerHTML = v.pages.map((p, i) => `<div class="page-wrap">
      <div class="page" style="aspect-ratio:${p.w}/${p.h}">
        <img src="/api/jobs/${v.id}/page/${i}.jpg" alt="Strona ${i + 1}" loading="${i < 2 ? "eager" : "lazy"}" decoding="async">
        <div class="marks" data-page="${i}"></div>
      </div>
      <div class="page-label"><span>Strona ${i + 1} z ${n}</span><span>${p.kind === "ocr" ? "skan, tekst z OCR" : ""}</span></div>
    </div>`).join("");
  const layers = [...el.querySelectorAll(".marks")];
  v.fields.forEach((f, fi) => f.boxes.forEach((b) => {
    const m = document.createElement("button");
    m.type = "button";
    m.tabIndex = -1;
    m.className = `mark prov-${f.prov}${b.approx ? " approx" : ""}`;
    m.dataset.fi = fi;
    m.style.cssText = `left:${b.x0 * 100}%;top:${b.y0 * 100}%;width:${(b.x1 - b.x0) * 100}%;height:${(b.y1 - b.y0) * 100}%`;
    m.setAttribute("aria-label", `${f.key}: ${display(f.value)}`);
    m.title = `${f.key}: ${display(f.value)}`;
    m.addEventListener("click", () => select(S.active === fi ? -1 : fi, { scroll: "panel" }));
    m.addEventListener("mouseenter", () => hot(fi, true));
    m.addEventListener("mouseleave", () => hot(fi, false));
    layers[b.p]?.appendChild(m);
  }));
}

function hot(fi, on) {
  document.querySelectorAll(`.mark[data-fi="${fi}"], .field[data-fi="${fi}"]`).forEach((e) => e.classList.toggle("hot", on));
}

function select(fi, { scroll = "page", force = false } = {}) {
  const prev = S.active;
  S.active = fi;
  if (prev !== fi || force) renderFields();
  document.querySelectorAll(".mark.on").forEach((m) => m.classList.remove("on"));
  $("#pages").classList.toggle("dim", fi >= 0);
  setHash();
  if (fi < 0) return;
  const marks = [...document.querySelectorAll(`.mark[data-fi="${fi}"]`)];
  void document.body.offsetWidth; // restart the stroke animation
  marks.forEach((m) => m.classList.add("on"));
  const behavior = reduced ? "auto" : "smooth";
  if (scroll === "page" && marks[0]) marks[0].scrollIntoView({ block: "center", behavior });
  const btn = $(`.field[data-fi="${fi}"]`);
  if (scroll === "panel" && btn) btn.scrollIntoView({ block: "nearest", behavior });
}

// --- JSON ---------------------------------------------------------------------------------------------------
function renderJson() {
  const v = S.view;
  const txt = JSON.stringify(v.output, null, 2);
  $("#json").innerHTML = esc(txt).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?)\b|\b(null|true|false)\b/g,
    (m, str, colon, n, lit) => str ? (colon ? `<span class="jk">${str}</span>${colon}` : `<span class="js">${str}</span>`) : n ? `<span class="jn">${n}</span>` : `<span class="jl">${lit}</span>`);
  const a = $("#json-download");
  a.href = `/api/jobs/${v.id}/output.json`;
  a.setAttribute("download", v.file.replace(/\.pdf$/i, "") + ".json");
}

async function copyJson() {
  if (!S.view) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(S.view.output, null, 2));
    $("#json-note").textContent = "Skopiowano JSON.";
  } catch { $("#json-note").textContent = "Przeglądarka nie pozwoliła skopiować. Zaznacz tekst ręcznie."; }
  setTimeout(() => ($("#json-note").textContent = ""), 3000);
}

// --- document types (config) ------------------------------------------------------------------------------
function renderTypes(problems = [], note = "", failTitle = "Konfiguracja ma błędy, zostawiam poprzednie typy") {
  const ts = S.state.doctypes;
  const on = ts.filter((t) => t.enabled);
  const st = $("#types-status");
  st.className = "types-status" + (problems.length ? " bad" : "");
  st.textContent = problems.length ? `${failTitle}:\n${problems.join("\n")}`
    : note || `Konfiguracja poprawna. Aktywne typy: ${on.length ? on.map((t) => typeName(t.name)).join(", ") : "żaden (każdy dokument da {})"}.`;
  $("#types").innerHTML = ts.map((t) => `<li class="type">
      <div class="type-head">
        <label class="switch"><input type="checkbox" role="switch" data-id="${esc(t.id)}" ${t.enabled ? "checked" : ""} ${t.error ? "disabled" : ""}>
          <span class="switch-track" aria-hidden="true"></span>
          <span><span class="type-name">${esc(typeName(t.name))}</span> <span class="type-file">${esc(t.path)}</span></span></label>
        <span class="type-tag">${GROUP_TAG[t.group] || t.group}</span>
      </div>
      ${t.description ? `<p class="type-desc">${esc(t.description)}</p>` : ""}
      ${t.error ? `<p class="type-err">Nie da się wczytać pliku: ${esc(t.error)}</p>` : ""}
      <div class="type-actions"><button type="button" class="type-yaml-btn" data-yaml="${esc(t.id)}" aria-expanded="false">Pokaż YAML</button>${t.group === "added" ? `<button type="button" class="type-yaml-btn type-remove" data-remove="${esc(t.id)}">Usuń</button>` : ""}</div>
      <pre class="type-yaml" hidden></pre>
    </li>`).join("");
  $("#types").querySelectorAll("input[data-id]").forEach((c) => c.addEventListener("change", saveTypes));
  $("#types").querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => removeType(b.dataset.remove)));
  $("#types").querySelectorAll("[data-yaml]").forEach((b) => b.addEventListener("click", async () => {
    const pre = b.parentElement.nextElementSibling;
    if (pre.hidden && !pre.textContent) {
      try { pre.textContent = await api(`/api/doctypes/${encodeURIComponent(b.dataset.yaml)}/yaml`); } catch (e) { return toast(e.message); }
    }
    pre.hidden = !pre.hidden;
    b.textContent = pre.hidden ? "Pokaż YAML" : "Ukryj YAML";
    b.setAttribute("aria-expanded", !pre.hidden);
  }));
}

async function addType(file) {
  try {
    const r = await api("/api/doctypes/upload", { method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/yaml" } });
    S.state = r;
    const added = r.doctypes.find((t) => t.id === `added:${file.name.replace(/[^\w.\-]/g, "_")}`);
    renderTypes([], added ? `Dodano typ ${typeName(added.name)} (${added.path}). Włącz go przełącznikiem, a potem przetwórz dokument ponownie.` : "");
  } catch (e) {
    renderTypes(e.body?.problems || [e.message], "", "Nie dodano typu z pliku " + file.name);
  }
  $("#type-file").value = "";
}

async function removeType(id) {
  try {
    S.state = await api(`/api/doctypes/${encodeURIComponent(id)}/remove`, { method: "POST" });
    renderTypes();
    markDirty();
  } catch (e) { renderTypes(e.body?.problems || [e.message]); }
}

async function saveTypes() {
  const enabled = [...$("#types").querySelectorAll("input[data-id]:checked")].map((c) => c.dataset.id);
  try {
    const r = await api("/api/doctypes", { method: "POST", body: JSON.stringify({ enabled }), headers: { "Content-Type": "application/json" } });
    S.state = r;
    renderTypes();
    markDirty();
  } catch (e) {
    S.state = e.body && e.body.doctypes ? e.body : S.state;
    renderTypes(e.body?.problems || [e.message]);
  }
}

init().catch((e) => toast(`Nie udało się połączyć z serwerem: ${e.message}`));
