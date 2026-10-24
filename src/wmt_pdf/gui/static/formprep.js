// «Prepara modulo»: i campi da creare si vedono come riquadri sulla pagina. Si possono far proporre (trattini,
// quadratini, linee e riquadri del PDF), disegnare a mano trascinando sulla pagina, spostare, ridimensionare,
// rinominare, cambiare di tipo o togliere; «Crea campi» li aggiunge al documento come versione nuova.
import { $, api, el, t, toast, upload } from "./common.js";

const KINDS = ["text", "checkbox", "date", "signature"];
const DEFAULT = { text: [180, 16], checkbox: [12, 12], date: [90, 16], signature: [160, 40] }; // punti PDF
const MIN_DRAW = 6; // pixel: sotto è un clic, e il campo prende la misura predefinita
const MIN_SIDE = 8; // punti: un campo più stretto o più basso non si potrebbe usare

const prep = {
  active: false,
  docId: null,
  fields: [], // { id, page, rect: [x0, y0, x1, y1] in punti PDF, name, type, multiline, tooltip }
  existing: [], // { page, rect } dei campi che il PDF ha già: si mostrano, non si toccano
  names: new Set(), // nomi già usati nel PDF (minuscoli)
  proposals: null, // risposta di /detect, chiesta all'apertura
  stash: new Map(), // id del documento → campi preparati e lasciati cambiando scheda
  selected: null,
  nextId: 1,
  hooks: null, // { apply, message, current: () => ({ id, page, viewport }), redraw }
};

export function initFormPrep(hooks) {
  prep.hooks = hooks;
  $("prep-open").addEventListener("click", () => open());
  $("prep-detect").addEventListener("click", detect);
  $("prep-cancel").addEventListener("click", () => close(true));
  $("prep-apply").addEventListener("click", applyFields);
  $("prep-name").addEventListener("input", () => edit({ name: $("prep-name").value.trim() }));
  $("prep-type").addEventListener("change", () => edit({ type: $("prep-type").value }));
  $("prep-multi").addEventListener("change", () => edit({ multiline: $("prep-multi").checked }));
  $("prep-delete").addEventListener("click", () => removeField(prep.selected));
  $("prep-layer").addEventListener("pointerdown", startDraw);
  document.addEventListener("keydown", (event) => {
    if (!prep.active || prep.selected === null || $("doc").hidden) return; // nell'organizzatore Canc toglie pagine
    if (event.target.closest?.("input, textarea, select")) return;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      removeField(prep.selected);
    } else if (event.key === "Escape") {
      select(null);
    }
  });
}

export const formPrepActive = () => prep.active;

function open(fields = []) {
  const current = prep.hooks.current();
  if (!current) return;
  Object.assign(prep, { active: true, docId: current.id, fields, selected: null, existing: [], names: new Set(),
    proposals: null });
  document.body.classList.add("preparing");
  $("prep-panel").hidden = false;
  $("prep-open").hidden = true;
  refresh();
  draw();
  loadDocumentFields(current.id);
}

// I campi che il PDF ha già (da mostrare e da non duplicare) e le proposte, in sottofondo.
async function loadDocumentFields(docId) {
  try {
    const spec = await api(`/api/doc/${docId}/detect`);
    if (!prep.active || prep.docId !== docId) return; // nel frattempo si è chiuso o cambiato documento
    prep.proposals = spec;
    prep.existing = spec.existing || [];
    prep.names = new Set((spec.names || []).map((n) => n.toLowerCase()));
    draw();
  } catch {
    // senza: si possono comunque disegnare i campi; «Riconosci campi» riproverà
  }
}

function close(ask = false) {
  if (ask && prep.fields.length && !window.confirm(t("Lasciare «Prepara modulo» senza creare i campi?"))) return;
  if (prep.docId) prep.stash.delete(prep.docId);
  Object.assign(prep, { active: false, docId: null, fields: [], selected: null, existing: [], proposals: null });
  document.body.classList.remove("preparing");
  $("prep-panel").hidden = true;
  $("prep-open").hidden = false;
  $("prep-layer").innerHTML = "";
}

async function detect() {
  const docId = prep.docId;
  if (!docId) return;
  $("prep-detect").disabled = true;
  prep.hooks.message(t("Ricerca dei campi…"));
  try {
    const spec = prep.proposals || await api(`/api/doc/${docId}/detect`);
    if (!prep.active || prep.docId !== docId) return; // documento cambiato mentre si cercava
    prep.proposals = spec;
    let added = 0;
    for (const item of spec.fields) {
      // Non due volte lo stesso campo: una proposta che copre un riquadro già presente si salta.
      if (prep.fields.some((f) => f.page === item.page && overlap(f.rect, item.rect) > 0.5)) continue;
      prep.fields.push({ id: prep.nextId++, page: item.page, rect: item.rect, name: uniqueName(item.name),
        type: item.type, multiline: Boolean(item.multiline), tooltip: item.tooltip || "" });
      added++;
    }
    const skipped = (spec.skipped || []).map((s) => s.page).join(", ");
    prep.hooks.message((added
      ? t("{n} campi proposti: controllali sulle pagine, poi «Crea campi»", { n: added })
      : t("Nessuno spazio da riempire riconosciuto: disegna i campi trascinando sulla pagina"))
      + (skipped ? ` ${t("Pagine non esaminate (ruotate o illeggibili): {pages}", { pages: skipped })}` : ""));
  } catch (error) {
    prep.hooks.message(error.message, true);
  } finally {
    $("prep-detect").disabled = false;
  }
  refresh();
  draw();
}

// --- disegno sulla pagina ------------------------------------------------------------------------------------------

// Chiamata da doc.js dopo ogni disegno della pagina (cambio pagina, zoom, versione nuova).
export function drawFormPrep() {
  const current = prep.hooks.current();
  if (prep.active && current?.id !== prep.docId) {
    // Scheda cambiata: i campi preparati restano in sospeso e tornano quando si torna al documento.
    const { docId, fields } = prep;
    close();
    if (fields.length) prep.stash.set(docId, fields);
  }
  if (!prep.active && current && prep.stash.has(current.id)) {
    const fields = prep.stash.get(current.id);
    prep.stash.delete(current.id);
    open(fields);
    prep.nextId = Math.max(prep.nextId, ...fields.map((f) => f.id + 1));
    return;
  }
  draw();
}

function draw() {
  const layer = $("prep-layer");
  layer.innerHTML = "";
  if (!prep.active) return;
  const { page, viewport } = prep.hooks.current();
  for (const item of prep.existing.filter((f) => f.page === page)) {
    const old = el("div", { class: "prep-existing", title: t("Campo già presente nel PDF") });
    place(old, screenRect(item.rect, viewport));
    layer.append(old);
  }
  for (const field of prep.fields.filter((f) => f.page === page)) layer.append(box(field, viewport));
  const others = prep.fields.length - prep.fields.filter((f) => f.page === page).length;
  $("prep-info").textContent = prep.fields.length
    ? t("{n} campi da creare ({m} su altre pagine). Trascina sulla pagina per aggiungerne uno.",
      { n: prep.fields.length, m: others })
    : t("Trascina sulla pagina per disegnare un campo, oppure «Riconosci campi».");
}

function screenRect(rect, viewport) {
  const [x0, y0] = viewport.convertToViewportPoint(rect[0], rect[1]);
  const [x1, y1] = viewport.convertToViewportPoint(rect[2], rect[3]);
  return { left: Math.min(x0, x1), top: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
}

function pdfRect(left, top, width, height, viewport) {
  const [x0, y0] = viewport.convertToPdfPoint(left, top + height);
  const [x1, y1] = viewport.convertToPdfPoint(left + width, top);
  const r = (v) => Math.round(v * 100) / 100;
  return [r(Math.min(x0, x1)), r(Math.min(y0, y1)), r(Math.max(x0, x1)), r(Math.max(y0, y1))];
}

function place(node, s) {
  Object.assign(node.style, { left: `${s.left}px`, top: `${s.top}px`, width: `${s.width}px`, height: `${s.height}px` });
}

function box(field, viewport) {
  const node = el("div", { class: `prep-box prep-${field.type}`, "data-id": String(field.id) });
  node.classList.toggle("selected", field.id === prep.selected);
  node.classList.toggle("multiline", field.multiline);
  node.title = field.tooltip ? `${field.name} · ${field.tooltip}` : field.name;
  place(node, screenRect(field.rect, viewport));
  const label = el("span", { class: "prep-label" });
  label.textContent = field.name;
  const handle = el("span", { class: "prep-handle", title: t("Trascina per cambiare la misura") });
  node.append(label, handle);
  node.addEventListener("pointerdown", (event) => startMove(event, field, node, event.target === handle));
  return node;
}

// Trascinando sul vuoto si disegna un campo nuovo; un clic lo mette della misura predefinita.
function startDraw(event) {
  if (event.target !== $("prep-layer") || event.button !== 0) return;
  event.preventDefault();
  select(null);
  const layer = $("prep-layer");
  const origin = layer.getBoundingClientRect();
  const x = event.clientX - origin.left;
  const y = event.clientY - origin.top;
  const ghost = el("div", { class: "prep-box prep-ghost" });
  layer.append(ghost);
  let rect = { left: x, top: y, width: 0, height: 0 };
  layer.setPointerCapture(event.pointerId);
  const move = (e) => {
    const cx = e.clientX - origin.left;
    const cy = e.clientY - origin.top;
    rect = { left: Math.min(x, cx), top: Math.min(y, cy), width: Math.abs(cx - x), height: Math.abs(cy - y) };
    place(ghost, rect);
  };
  const stop = () => {
    layer.removeEventListener("pointermove", move);
    layer.removeEventListener("pointerup", up);
    layer.removeEventListener("pointercancel", stop);
    ghost.remove();
  };
  const up = () => {
    stop();
    const { page, viewport } = prep.hooks.current();
    const type = $("prep-kind").value;
    let pdf;
    if (rect.width < MIN_DRAW && rect.height < MIN_DRAW) {
      const [w, h] = DEFAULT[type];
      const [px, py] = viewport.convertToPdfPoint(x, y);
      pdf = [px, py - h / 2, px + w, py + h / 2].map((v) => Math.round(v * 100) / 100);
    } else {
      pdf = pdfRect(rect.left, rect.top, rect.width, rect.height, viewport);
      // Mai più stretto o più basso di MIN_SIDE punti; una casella è quadrata.
      pdf[2] = Math.max(pdf[2], pdf[0] + MIN_SIDE);
      pdf[1] = Math.min(pdf[1], pdf[3] - MIN_SIDE);
      if (type === "checkbox") {
        const side = Math.max(MIN_SIDE, Math.min(pdf[2] - pdf[0], pdf[3] - pdf[1]));
        pdf = [pdf[0], pdf[3] - side, pdf[0] + side, pdf[3]];
      }
    }
    const field = { id: prep.nextId++, page, rect: pdf, name: uniqueName(type === "checkbox" ? "casella" : "campo"),
      type, multiline: type === "text" && pdf[3] - pdf[1] > 30, tooltip: "" };
    prep.fields.push(field);
    prep.selected = field.id;
    refresh();
    draw();
    select(field.id);
    $("prep-name").focus();
    $("prep-name").select();
  };
  layer.addEventListener("pointermove", move);
  layer.addEventListener("pointerup", up);
  layer.addEventListener("pointercancel", stop); // tocco o penna interrotti: nessun campo
}

function startMove(event, field, node, resize) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  select(field.id);
  const start = { x: event.clientX, y: event.clientY, ...screenRect(field.rect, prep.hooks.current().viewport) };
  const target = $("prep-layer").querySelector(`[data-id="${field.id}"]`) || node;
  target.setPointerCapture(event.pointerId);
  let moved = false;
  const move = (e) => {
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
    const s = resize
      ? { left: start.left, top: start.top, width: Math.max(8, start.width + dx), height: Math.max(8, start.height + dy) }
      : { left: start.left + dx, top: start.top + dy, width: start.width, height: start.height };
    place(target, s);
    target.dataset.rect = JSON.stringify(s);
  };
  const stop = () => {
    target.removeEventListener("pointermove", move);
    target.removeEventListener("pointerup", up);
    target.removeEventListener("pointercancel", cancel);
  };
  const cancel = () => {
    stop();
    draw(); // il riquadro torna dov'era
  };
  const up = () => {
    stop();
    if (!moved || !target.dataset.rect) return;
    const s = JSON.parse(target.dataset.rect);
    const rect = pdfRect(s.left, s.top, s.width, s.height, prep.hooks.current().viewport);
    field.rect = [rect[0], Math.min(rect[1], rect[3] - MIN_SIDE), Math.max(rect[2], rect[0] + MIN_SIDE), rect[3]];
    draw();
  };
  target.addEventListener("pointermove", move);
  target.addEventListener("pointerup", up);
  target.addEventListener("pointercancel", cancel);
}

// --- selezione e modifica --------------------------------------------------------------------------------------------

function select(id) {
  prep.selected = id;
  const field = prep.fields.find((f) => f.id === id);
  $("prep-edit").hidden = !field;
  if (field) {
    $("prep-name").value = field.name;
    $("prep-type").value = field.type;
    $("prep-multi").checked = field.multiline;
    $("prep-multi").disabled = field.type !== "text";
  }
  document.querySelectorAll(".prep-box").forEach((b) => b.classList.toggle("selected", Number(b.dataset.id) === id));
}

function edit(changes) {
  const field = prep.fields.find((f) => f.id === prep.selected);
  if (!field) return;
  Object.assign(field, changes);
  if (field.type !== "text") field.multiline = false;
  $("prep-multi").disabled = field.type !== "text";
  draw();
}

function removeField(id) {
  prep.fields = prep.fields.filter((f) => f.id !== id);
  select(null);
  refresh();
  draw();
}

function refresh() {
  $("prep-apply").disabled = !prep.fields.length;
  $("prep-apply").textContent = prep.fields.length ? t("Crea {n} campi", { n: prep.fields.length }) : t("Crea campi");
}

function uniqueName(base) {
  const taken = new Set([...prep.names, ...prep.fields.map((f) => f.name.toLowerCase())]);
  if (!taken.has(base.toLowerCase()) && !/^(campo|casella)$/.test(base)) return base;
  for (let n = 1; ; n++) {
    const name = `${base}_${n}`;
    if (!taken.has(name.toLowerCase())) return name;
  }
}

function overlap(a, b) {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  if (w <= 0 || h <= 0) return 0;
  const smaller = Math.min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]));
  return smaller ? (w * h) / smaller : 0;
}

// --- creazione ------------------------------------------------------------------------------------------------------

function problem() {
  const seen = new Set();
  for (const field of prep.fields) {
    const key = field.name.toLowerCase();
    if (!field.name) return { field, text: t("Un campo senza nome: scrivine uno") };
    if (field.name.includes(".")) return { field, text: t("«{name}»: niente punti nel nome (usa _ o -)", { name: field.name }) };
    if (seen.has(key)) return { field, text: t("«{name}»: due campi con lo stesso nome", { name: field.name }) };
    if (prep.names.has(key)) return { field, text: t("«{name}»: il PDF ha già un campo con questo nome", { name: field.name }) };
    seen.add(key);
  }
  return null;
}

async function applyFields() {
  const wrong = problem();
  if (wrong) {
    toast(wrong.text);
    prep.hooks.goTo(wrong.field.page);
    select(wrong.field.id);
    return;
  }
  const spec = {
    fields: prep.fields.map((f) => ({
      name: f.name, type: f.type, page: f.page, rect: f.rect,
      ...(f.multiline ? { multiline: true } : {}), ...(f.tooltip ? { tooltip: f.tooltip } : {}),
    })),
  };
  let info;
  try {
    info = await upload(new File([JSON.stringify(spec)], "campi.json", { type: "application/json" }));
  } catch (error) {
    prep.hooks.message(error.message, true);
    return;
  }
  $("prep-apply").disabled = true;
  const created = await prep.hooks.apply("form", { auto: false, spec: info.id });
  if (created) close();
  else refresh();
}
