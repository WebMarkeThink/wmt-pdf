// «Organizza pagine»: le pagine di uno o più file in una griglia di miniature da riordinare trascinandole,
// selezionare, ruotare, eliminare, con altri file da inserire in un punto qualsiasi. Serve sia al documento aperto
// (il risultato diventa la sua versione nuova) sia a «Unisci» (il risultato si apre in una scheda nuova).
import { $, accepts, el, fetchBlob, post, t, toast, upload } from "./common.js";
import { PDF_OPTIONS, apply, currentDoc, openDocument, pdfjs } from "./doc.js";

const THUMB = 150; // larghezza delle miniature in pixel CSS
const org = {
  mode: null, // "doc": il documento aperto; "merge": file da unire in un PDF nuovo
  back: "home", // vista a cui tornare con «Annulla»
  sources: [], // { fileId, name, pdf }
  pages: [], // { key, src, page, rot }
  selected: new Set(),
  anchor: null, // ultima pagina cliccata (per Maiusc+clic)
  cards: new Map(), // key → elemento della griglia (spostato, non ridisegnato)
  dragging: false,
  busy: false,
  nextKey: 1,
  observer: null,
};
let ctx = null;
let accept = [];

export function initOrganizer(context) {
  ctx = context;
  const merge = ctx.tools().find((tool) => tool.id === "merge");
  accept = merge?.accept || [];
  if (accept.length && !accept.includes("*")) $("org-input").accept = accept.join(",");
  $("org-add").addEventListener("click", () => $("org-input").click());
  $("org-input").addEventListener("change", () => {
    const files = [...$("org-input").files];
    $("org-input").value = "";
    addFiles(files, org.pages.length);
  });
  $("org-rotl").addEventListener("click", () => rotateSelected(-90));
  $("org-rotr").addEventListener("click", () => rotateSelected(90));
  $("org-delete").addEventListener("click", deleteSelected);
  $("org-all").addEventListener("click", selectAll);
  $("org-cancel").addEventListener("click", () => ctx.show(org.back));
  $("org-apply").addEventListener("click", applyChanges);
  $("org-preview-close").addEventListener("click", () => $("org-preview").close());
  const grid = $("org-grid");
  grid.addEventListener("click", (event) => {
    if (!event.target.closest(".org-card")) clearSelection(); // clic sullo sfondo: niente selezionato
  });
  grid.addEventListener("dragover", onDragOver);
  grid.addEventListener("dragleave", (event) => {
    if (!grid.contains(event.relatedTarget)) clearDropMark();
  });
  grid.addEventListener("drop", onDrop);
  document.addEventListener("keydown", onKey);
  org.observer = new IntersectionObserver((entries) => {
    for (const entry of entries) if (entry.isIntersecting) drawThumb(entry.target);
  }, { root: grid, rootMargin: "300px" });
}

// --- aprire la vista ---------------------------------------------------------------------------------------

export async function organizeDocument({ pickFiles = false } = {}) {
  const state = currentDoc();
  if (!state || org.busy) return;
  await open("doc", "doc", [{ id: state.file_id, name: state.name, kind: "pdf" }]);
  if (org.sources[0]?.fileId !== state.file_id) {
    ctx.show("doc"); // il documento non si è caricato: l'errore è già nell'avviso
    return;
  }
  if (pickFiles) $("org-input").click();
}

export async function organizeFiles(infos = [], back = "home") {
  if (org.busy) return;
  await open("merge", back, infos);
}

async function open(mode, back, infos) {
  reset();
  org.mode = mode;
  org.back = back;
  $("org-title").textContent = mode === "doc" ? t("Organizza pagine") : t("Unisci e organizza");
  $("org-apply").textContent = mode === "doc" ? t("Applica al documento") : t("Crea il PDF");
  ctx.show("organize");
  await addInfos(infos, 0);
  refresh();
}

function reset() {
  for (const source of org.sources) source.pdf.loadingTask.destroy();
  Object.assign(org, { sources: [], pages: [], anchor: null, dragging: false, busy: false });
  org.selected.clear();
  org.cards.clear();
  org.observer?.disconnect();
  $("org-grid").innerHTML = "";
}

// --- file e pagine ------------------------------------------------------------------------------------------

async function loadSource(fileId, name) {
  const data = new Uint8Array(await (await fetchBlob(fileId)).arrayBuffer());
  const pdf = await pdfjs.getDocument({ data, ...PDF_OPTIONS }).promise;
  return { fileId, name, pdf };
}

// Word, immagini, email…: si convertono subito in PDF, così se ne vedono le pagine.
async function asPdf(info) {
  if (info.kind === "pdf" || accepts([".pdf"], info.name)) return info;
  const result = await post("/api/run", { tool: "convert", inputs: [info.id], options: {} });
  const out = (result.outputs || []).find((o) => o.kind === "pdf");
  if (result.code !== 0 || !out) throw new Error(result.stderr || t("conversione non riuscita"));
  return { ...out, name: info.name };
}

async function addInfos(infos, at) {
  if (!infos.length) return;
  setBusy(true, infos.length === 1 ? t("Caricamento…") : t("Caricamento di {n} file…", { n: infos.length }));
  const added = [];
  try {
    for (const info of infos) {
      try {
        const pdfInfo = await asPdf(info);
        const source = await loadSource(pdfInfo.id, info.name);
        org.sources.push(source);
        const src = org.sources.length - 1;
        for (let n = 1; n <= source.pdf.numPages; n++) added.push({ key: org.nextKey++, src, page: n, rot: 0 });
      } catch (error) {
        toast(t("{name}: {error}", { name: info.name, error: error.message }));
      }
    }
  } finally {
    setBusy(false);
  }
  const adding = org.pages.length > 0; // all'apertura no; dopo, i file inseriti restano selezionati: si vedono
  org.pages.splice(Math.min(at, org.pages.length), 0, ...added);
  if (adding) org.selected = new Set(added.map((p) => p.key));
  refresh();
}

async function addFiles(files, at) {
  if (org.busy) return; // un caricamento alla volta: l'indice d'inserimento resta quello giusto
  const usable = files.filter((f) => accept.includes("*") || accepts(accept, f.name));
  for (const f of files) if (!usable.includes(f)) toast(t("{name}: formato non supportato", { name: f.name }));
  if (!usable.length) return;
  setBusy(true, t("Caricamento di {n} file…", { n: usable.length }));
  let infos;
  try {
    infos = await Promise.all(usable.map((f) => upload(f)));
  } catch (error) {
    toast(error.message);
    setBusy(false);
    return;
  }
  setBusy(false);
  await addInfos(infos, at);
}

// --- griglia -------------------------------------------------------------------------------------------------

function card(item) {
  let node = org.cards.get(item.key);
  if (node) return node;
  node = el("div", { class: "org-card", draggable: "true", role: "option", "data-key": String(item.key) });
  const thumb = el("div", { class: "org-thumb" });
  thumb.append(el("canvas"));
  const label = el("div", { class: "org-label" });
  label.append(el("span", { class: "org-n" }), el("span", { class: "org-src" }));
  const tools = el("div", { class: "org-tools" });
  const button = (title, path, onClick) => {
    const b = el("button", { type: "button", title, "aria-label": title },
      `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`);
    b.addEventListener("click", (event) => {
      event.stopPropagation();
      onClick();
    });
    return b;
  };
  tools.append(
    button(t("Ruota a sinistra"), "M4 11a8 8 0 1 1 2.3 5.7M4 4v7h7", () => rotate([item.key], -90)),
    button(t("Ruota a destra"), "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7", () => rotate([item.key], 90)),
    button(t("Elimina"), "M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3", () => remove([item.key])),
  );
  node.append(thumb, label, tools);
  node.addEventListener("click", (event) => onCardClick(event, item.key));
  node.addEventListener("dblclick", () => preview(item.key));
  node.addEventListener("dragstart", (event) => onDragStart(event, item.key));
  node.addEventListener("dragend", () => {
    org.dragging = false;
    clearDropMark();
    document.querySelectorAll(".org-card.dragged").forEach((c) => c.classList.remove("dragged"));
  });
  org.cards.set(item.key, node);
  return node;
}

function refresh() {
  const grid = $("org-grid");
  const multi = org.sources.length > 1;
  org.pages.forEach((item, index) => {
    const node = card(item);
    node.querySelector(".org-n").textContent = String(index + 1);
    const source = org.sources[item.src];
    node.querySelector(".org-src").textContent = multi ? `${source.name} · p. ${item.page}` : `p. ${item.page}`;
    node.title = `${source.name} · ${t("pagina {n}", { n: item.page })}`;
    node.classList.toggle("selected", org.selected.has(item.key));
    node.setAttribute("aria-selected", String(org.selected.has(item.key)));
    grid.append(node); // spostare l'elemento lo mette nel posto giusto senza ridisegnarlo
    org.observer.observe(node);
  });
  for (const [key, node] of org.cards) {
    if (!org.pages.some((p) => p.key === key)) {
      org.observer.unobserve(node);
      node.remove();
      org.cards.delete(key);
    }
  }
  const count = org.pages.length;
  const selected = org.selected.size;
  $("org-count").textContent = count
    ? `${count === 1 ? t("1 pagina") : t("{n} pagine", { n: count })}${selected ? ` · ${t("{n} selezionate", { n: selected })}` : ""}`
    : "";
  $("org-empty").hidden = count > 0;
  $("org-apply").disabled = !count || org.busy;
  for (const id of ["org-rotl", "org-rotr", "org-delete"]) $(id).disabled = !selected;
}

// Ogni disegno usa una tela nuova, messa al posto della vecchia solo se è ancora l'ultimo chiesto: due rotazioni
// di seguito non si contendono la stessa tela e la miniatura mostra sempre la rotazione giusta.
async function drawThumb(node) {
  const item = org.pages.find((p) => p.key === Number(node.dataset.key));
  if (!item || node.dataset.drawn === `${item.rot}`) return;
  node.dataset.drawn = `${item.rot}`;
  const token = (node.drawToken || 0) + 1;
  node.drawToken = token;
  try {
    const page = await org.sources[item.src].pdf.getPage(item.page);
    const rotation = (page.rotate + item.rot) % 360;
    const base = page.getViewport({ scale: 1, rotation });
    const ratio = window.devicePixelRatio || 1;
    const fit = Math.min(THUMB / base.width, (THUMB * 1.3) / base.height);
    const viewport = page.getViewport({ scale: fit * ratio, rotation });
    const canvas = el("canvas");
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
    canvas.style.height = `${Math.floor(viewport.height / ratio)}px`;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    if (node.drawToken === token) node.querySelector("canvas").replaceWith(canvas);
  } catch {
    if (node.drawToken === token) node.dataset.drawn = ""; // si riprova alla prossima occasione
  }
}

// --- selezione -------------------------------------------------------------------------------------------------

function onCardClick(event, key) {
  if (event.shiftKey && org.anchor !== null) {
    const keys = org.pages.map((p) => p.key);
    const [a, b] = [keys.indexOf(org.anchor), keys.indexOf(key)].sort((x, y) => x - y);
    if (!(event.ctrlKey || event.metaKey)) org.selected.clear();
    keys.slice(a, b + 1).forEach((k) => org.selected.add(k));
  } else if (event.ctrlKey || event.metaKey) {
    if (org.selected.has(key)) org.selected.delete(key);
    else org.selected.add(key);
    org.anchor = key;
  } else {
    org.selected = new Set([key]);
    org.anchor = key;
  }
  refresh();
}

function clearSelection() {
  org.selected.clear();
  refresh();
}

function selectAll() {
  org.selected = new Set(org.pages.map((p) => p.key));
  refresh();
}

// --- modifiche --------------------------------------------------------------------------------------------------

function rotate(keys, angle) {
  for (const item of org.pages) if (keys.includes(item.key)) item.rot = (item.rot + angle + 360) % 360;
  for (const key of keys) {
    const node = org.cards.get(key);
    if (node) drawThumb(node);
  }
  refresh();
}

function rotateSelected(angle) {
  rotate([...org.selected], angle);
}

function remove(keys) {
  org.pages = org.pages.filter((p) => !keys.includes(p.key));
  keys.forEach((k) => org.selected.delete(k));
  if (keys.includes(org.anchor)) org.anchor = null;
  refresh();
}

function deleteSelected() {
  remove([...org.selected]);
}

function onKey(event) {
  if (ctx.view() !== "organize" || event.target.closest?.("input, textarea, select") || document.querySelector("dialog[open]")) {
    return;
  }
  const mod = event.ctrlKey || event.metaKey;
  if ((event.key === "Delete" || event.key === "Backspace") && org.selected.size) {
    event.preventDefault();
    deleteSelected();
  } else if (mod && event.key.toLowerCase() === "a") {
    event.preventDefault();
    selectAll();
  } else if (event.key === "Escape" && org.selected.size) {
    event.preventDefault();
    clearSelection();
  }
}

// --- trascinamento ----------------------------------------------------------------------------------------------

function onDragStart(event, key) {
  if (!org.selected.has(key)) {
    org.selected = new Set([key]);
    org.anchor = key;
    refresh();
  }
  org.dragging = true;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", "wmt-pdf-pagine");
  for (const k of org.selected) org.cards.get(k)?.classList.add("dragged");
}

// Dove cadrebbe ciò che si trascina: prima o dopo la scheda sotto il puntatore.
function dropIndex(event) {
  const target = event.target.closest(".org-card");
  if (!target) return org.pages.length;
  const index = org.pages.findIndex((p) => p.key === Number(target.dataset.key));
  const box = target.getBoundingClientRect();
  return event.clientX > box.left + box.width / 2 ? index + 1 : index;
}

function clearDropMark() {
  document.querySelectorAll(".org-card.drop-before, .org-card.drop-after")
    .forEach((c) => c.classList.remove("drop-before", "drop-after"));
  $("org-grid").classList.remove("drop-end");
}

function onDragOver(event) {
  const files = event.dataTransfer.types.includes("Files");
  if (!org.dragging && !files) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = files ? "copy" : "move";
  clearDropMark();
  const index = dropIndex(event);
  if (index < org.pages.length) org.cards.get(org.pages[index].key)?.classList.add("drop-before");
  else if (org.pages.length) org.cards.get(org.pages[org.pages.length - 1].key)?.classList.add("drop-after");
  else $("org-grid").classList.add("drop-end");
}

function onDrop(event) {
  event.preventDefault();
  event.stopPropagation(); // non è un PDF da aprire come documento
  const index = dropIndex(event);
  clearDropMark();
  if (event.dataTransfer.files.length && !org.dragging) {
    addFiles([...event.dataTransfer.files], index);
    return;
  }
  if (!org.dragging) return;
  org.dragging = false;
  const moving = org.pages.filter((p) => org.selected.has(p.key));
  const before = org.pages.slice(0, index).filter((p) => !org.selected.has(p.key)).length;
  const rest = org.pages.filter((p) => !org.selected.has(p.key));
  rest.splice(before, 0, ...moving);
  org.pages = rest;
  refresh();
}

// --- anteprima ---------------------------------------------------------------------------------------------------

async function preview(key) {
  const item = org.pages.find((p) => p.key === key);
  if (!item) return;
  const source = org.sources[item.src];
  let page;
  try {
    page = await source.pdf.getPage(item.page);
  } catch (error) {
    toast(error.message);
    return;
  }
  const rotation = (page.rotate + item.rot) % 360;
  const base = page.getViewport({ scale: 1, rotation });
  const ratio = window.devicePixelRatio || 1;
  const fit = Math.min((window.innerWidth * 0.8) / base.width, (window.innerHeight * 0.8) / base.height);
  const viewport = page.getViewport({ scale: fit * ratio, rotation });
  const canvas = $("org-preview-canvas");
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
  canvas.style.height = `${Math.floor(viewport.height / ratio)}px`;
  $("org-preview-title").textContent = `${source.name} · ${t("pagina {n}", { n: item.page })}`;
  $("org-preview").showModal();
  await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise.catch(() => {});
}

// --- risultato ------------------------------------------------------------------------------------------------------

// «1:1-3,2:1@90»: pagine di fila dallo stesso file, con la stessa rotazione, diventano un intervallo.
export function sheetSpec(pages) {
  const parts = [];
  let run = null;
  for (const p of pages) {
    if (run && run.src === p.src && run.rot === p.rot && p.page === run.last + 1) {
      run.last = p.page;
      continue;
    }
    if (run) parts.push(run);
    run = { src: p.src, first: p.page, last: p.page, rot: p.rot };
  }
  if (run) parts.push(run);
  return parts.map((r) => `${r.src + 1}:${r.first}${r.last !== r.first ? `-${r.last}` : ""}${r.rot ? `@${r.rot}` : ""}`)
    .join(",");
}

function unchanged() {
  const single = org.sources.length === 1;
  return single && org.pages.length === org.sources[0].pdf.numPages &&
    org.pages.every((p, i) => p.src === 0 && p.page === i + 1 && !p.rot);
}

async function applyChanges() {
  if (!org.pages.length || org.busy) return;
  const spec = sheetSpec(org.pages);
  if (org.mode === "doc") {
    if (unchanged()) {
      ctx.show("doc");
      return;
    }
    if (currentDoc()?.file_id !== org.sources[0].fileId) {
      toast(t("Il documento è cambiato nel frattempo: riapri «Organizza pagine»"));
      return;
    }
    const before = currentDoc()?.version;
    setBusy(true, t("Elaborazione…"));
    try {
      await apply("assemble", { pages: spec }, { files: org.sources.slice(1).map((s) => s.fileId), position: "end" });
    } finally {
      setBusy(false);
    }
    if (currentDoc()?.version !== before) ctx.show("doc"); // riuscito: si torna al documento, con la versione nuova
    else toast(t("Le pagine non sono state cambiate: vedi il messaggio nel documento"));
    return;
  }
  setBusy(true, t("Elaborazione…"));
  try {
    const result = await post("/api/run", {
      tool: "assemble", inputs: org.sources.map((s) => s.fileId),
      options: { pages: spec, bookmarks: org.sources.length > 1 },
    });
    const out = (result.outputs || []).find((o) => o.kind === "pdf");
    if (result.code !== 0 || !out) throw new Error(result.stderr || t("operazione non riuscita"));
    await openDocument({ file_id: out.id });
  } catch (error) {
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

function setBusy(busy, text = "") {
  org.busy = busy;
  $("org-status").textContent = busy ? text : "";
  $("org-apply").disabled = busy || !org.pages.length;
  $("org-add").disabled = busy;
}

export const organizerBusy = () => org.busy;
