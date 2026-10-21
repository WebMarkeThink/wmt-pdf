// Vista documento: pagina con testo modificabile, miniature, strumenti, versioni e salvataggio.
import * as pdfjs from "/static/vendor/pdf.min.mjs";
import {
  $, api, collectOptions, desktop, downloadFile, el, fetchBlob, missingOptions, post, renderOptions, svg, toast, upload,
} from "./common.js";

pdfjs.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.mjs";
const VENDOR = "/static/vendor/";
export const PDF_OPTIONS = {
  isEvalSupported: false,
  enableScripting: false,
  wasmUrl: `${VENDOR}wasm/`, // decodificatori delle immagini delle scansioni (JBIG2, JPEG 2000)
  standardFontDataUrl: `${VENDOR}standard_fonts/`,
  cMapUrl: `${VENDOR}cmaps/`,
  cMapPacked: true,
};
export { pdfjs };

// Strumenti che nella vista documento hanno un comando dedicato o non si applicano a un solo PDF.
const NOT_IN_LIST = new Set(["merge", "convert", "p7m", "edittext", "addtext", "replace", "highlight", "rotate", "pages"]);

const doc = {
  state: null, pdf: null, page: 1, scale: 1.25, mode: "pointer", viewport: null, selected: null, reselect: null, boxesRun: 0, drawRun: 0,
  task: null, thumbsRun: 0, busy: false, dialogTool: null, dialogFiles: {},
};
let ctx = null;

export const currentDoc = () => doc.state;

export function initDocs(context) {
  ctx = context;
  document.querySelectorAll(".mode").forEach((button) =>
    button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("doc-prev").addEventListener("click", () => goTo(doc.page - 1));
  $("doc-next").addEventListener("click", () => goTo(doc.page + 1));
  $("doc-zoom-in").addEventListener("click", () => zoom(1.2));
  $("doc-zoom-out").addEventListener("click", () => zoom(1 / 1.2));
  $("overlay").addEventListener("click", onOverlayClick);
  $("page-rotate").addEventListener("click", () => apply("rotate", { angle: "90", pages: String(doc.page) }));
  $("page-delete").addEventListener("click", deletePage);
  const merge = ctx.tools().find((t) => t.id === "merge");
  if (merge && !merge.accept.includes("*")) $("merge-input").accept = merge.accept.join(",");
  $("doc-merge").addEventListener("click", () => $("merge-input").click());
  $("merge-input").addEventListener("change", () => {
    const files = [...$("merge-input").files];
    $("merge-input").value = "";
    if (files.length) chooseMergePosition(files);
  });
  $("md-cancel").addEventListener("click", () => $("merge-dialog").close());
  $("md-run").addEventListener("click", runMerge);
  $("find-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const find = $("find-text").value;
    if (find) apply("replace", { find, replacement: $("replace-text").value });
  });
  $("highlight-btn").addEventListener("click", () => {
    const query = $("find-text").value;
    if (query) apply("highlight", { query });
    else toast("Scrivi prima il testo da evidenziare");
  });
  $("pop-cancel").addEventListener("click", closePopover);
  $("td-cancel").addEventListener("click", () => $("tool-dialog").close());
  $("td-run").addEventListener("click", runDialogTool);
  window.addEventListener("resize", closePopover);
  renderToolList();
}

// --- apertura e caricamento ---------------------------------------------------------------

export async function openDocument(body) {
  if (doc.state?.modified &&
      !window.confirm(`«${doc.state.name}» ha modifiche non salvate. Vuoi aprire un altro documento e perderle?`)) {
    return;
  }
  try {
    const state = await post("/api/doc/open", body);
    doc.page = 1;
    ctx.show("doc"); // la vista compare subito; la pagina si disegna appena pronta
    await load(state);
  } catch (error) {
    toast(`Apertura non riuscita: ${error.message}`);
  }
}

async function load(state) {
  window.getSelection()?.removeAllRanges(); // nessuna selezione rimasta sopra la pagina dopo una modifica
  doc.state = state;
  const data = new Uint8Array(await (await fetchBlob(state.file_id)).arrayBuffer());
  if (doc.loading) await doc.loading.destroy(); // in PDF.js 6 si chiude dal task di caricamento
  doc.loading = pdfjs.getDocument({ data, ...PDF_OPTIONS });
  doc.pdf = await doc.loading.promise;
  doc.page = Math.min(Math.max(1, doc.page), doc.pdf.numPages);
  ctx.updateBar();
  await drawPage();
  renderThumbs();
}

// --- pagina ---------------------------------------------------------------------------------

async function drawPage() {
  if (!doc.pdf) return;
  closePopover();
  const run = ++doc.drawRun;
  const previous = doc.task;
  if (previous) {
    // PDF.js 6 non ridisegna sullo stesso canvas finché il disegno precedente non si è davvero fermato.
    previous.cancel();
    await previous.promise.catch(() => {});
  }
  const page = await doc.pdf.getPage(doc.page);
  if (run !== doc.drawRun) return; // nel frattempo è partito un disegno più recente
  const viewport = page.getViewport({ scale: doc.scale });
  doc.viewport = viewport;
  const ratio = window.devicePixelRatio || 1;
  const canvas = $("doc-canvas");
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;
  const holder = $("page-holder");
  holder.style.width = canvas.style.width;
  holder.style.height = canvas.style.height;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const task = page.render({ canvasContext: context, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE });
  doc.task = task;
  try {
    await task.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
    return;
  } finally {
    if (doc.task === task) doc.task = null;
  }
  $("doc-page").textContent = `${doc.page} / ${doc.pdf.numPages}`;
  $("doc-zoom").textContent = `${Math.round(doc.scale * 100)}%`;
  document.querySelectorAll(".thumb").forEach((t) => t.setAttribute("aria-current", String(Number(t.dataset.page) === doc.page)));
  await loadTextBoxes();
}

const KIND_NAMES = { text: "Testo", image: "Immagine", graphic: "Elemento grafico", annotation: "Annotazione" };

async function loadTextBoxes() {
  const run = ++doc.boxesRun; // due caricamenti di fila (pagina e modalità): vale solo l'ultimo
  const overlay = $("overlay");
  overlay.innerHTML = "";
  overlay.className = `overlay ${doc.mode}`;
  select(null);
  if (doc.mode === "add") return;
  const pointer = doc.mode === "pointer";
  let objects = [];
  try {
    const kind = pointer ? "objects" : "textobjects";
    objects = await api(`/api/doc/${doc.state.id}/${kind}?page=${doc.page}`);
  } catch (error) {
    if (pointer) message(error.message, true); // la pagina resta visibile; si può comunque aggiungere testo
    return;
  }
  if (run !== doc.boxesRun) return;
  const want = doc.reselect; // dopo uno spostamento resta selezionato l'oggetto spostato
  doc.reselect = null;
  let reselect = null;
  for (const item of objects) {
    const obj = pointer ? { ...item, text: item.label } : { ...item, kind: "text", label: item.text };
    // PDF.js 6 converte solo punti: il riquadro si ricava dai due angoli.
    const [x0, y0] = doc.viewport.convertToViewportPoint(obj.bbox[0], obj.bbox[1]);
    const [x1, y1] = doc.viewport.convertToViewportPoint(obj.bbox[2], obj.bbox[3]);
    const name = KIND_NAMES[obj.kind];
    let title = obj.label; // testo: il testo stesso
    if (obj.kind !== "text") title = obj.label === name ? name : `${name}: ${obj.label}`;
    const box = el("div", { class: `tbox kind-${obj.kind}`, title });
    Object.assign(box.style, {
      left: `${Math.min(x0, x1) - 1}px`, top: `${Math.min(y0, y1) - 1}px`,
      width: `${Math.max(Math.abs(x1 - x0), 4) + 2}px`, height: `${Math.max(Math.abs(y1 - y0), 4) + 2}px`,
    });
    box.addEventListener("pointerdown", (event) => startDrag(event, obj, box));
    box.addEventListener("click", (event) => event.stopPropagation());
    if (pointer && obj.kind === "text") box.addEventListener("dblclick", () => editObject(obj, box));
    overlay.append(box);
    // index -1: l'ultimo di quel tipo, dove finisce l'oggetto spostato.
    if (want?.kind === obj.kind && [-1, obj.index].includes(want.index)) reselect = { obj, box };
  }
  if (reselect && pointer) select(reselect.obj, reselect.box);
}

// Clic: in «Modifica testo» apre la modifica, in «Seleziona» seleziona. Trascinando: si sposta.
const DRAG_THRESHOLD = 4; // pixel: sotto questa soglia è un clic

function select(obj, box) {
  clearTimeout(doc.selected?.timer); // uno spostamento con le frecce in sospeso non vale più
  document.querySelectorAll(".tbox.selected").forEach((b) => b.classList.remove("selected"));
  doc.selected = obj ? { obj, box, timer: null, start: null } : null;
  if (!obj) return;
  box.classList.add("selected");
  message(`Selezionato: ${KIND_NAMES[obj.kind].toLowerCase()}. Trascinalo, oppure frecce per spostarlo (Maiusc: di più)${
    obj.kind === "text" ? "; doppio clic per modificarlo" : ""}`);
}

async function moveObject(obj, dxPdf, dyPdf) {
  const by = `${dxPdf.toFixed(2)},${dyPdf.toFixed(2)}`;
  const version = doc.state.version;
  if (doc.mode === "select") {
    await apply("movetext", { page: doc.page, index: obj.index, by });
  } else {
    // Dopo lo spostamento l'oggetto va in fondo alla pagina (le annotazioni restano dove sono nell'elenco).
    doc.reselect = { kind: obj.kind, index: obj.kind === "annotation" ? obj.index : -1 };
    await apply("move", { page: doc.page, kind: obj.kind, index: obj.index, by });
  }
  if (doc.state.version === version) { // rifiutato o non eseguito: il riquadro torna dov'è davvero l'oggetto
    doc.reselect = null;
    loadTextBoxes();
  }
}

// Dallo spostamento sullo schermo a quello in punti PDF (tiene conto di zoom e rotazione).
function pdfDelta(fromLeft, fromTop, toLeft, toTop) {
  const [x0, y0] = doc.viewport.convertToPdfPoint(fromLeft, fromTop);
  const [x1, y1] = doc.viewport.convertToPdfPoint(toLeft, toTop);
  return [x1 - x0, y1 - y0];
}

function startDrag(event, obj, box) {
  if (doc.mode === "add" || event.button !== 0 || doc.busy) return;
  event.preventDefault();
  event.stopPropagation();
  if (doc.mode === "pointer") select(obj, box);
  const overlay = $("overlay").getBoundingClientRect();
  const drag = {
    startX: event.clientX, startY: event.clientY,
    left: parseFloat(box.style.left), top: parseFloat(box.style.top), moved: false,
  };
  box.setPointerCapture(event.pointerId);
  const move = (e) => {
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!drag.moved) {
      drag.moved = true;
      closePopover();
      box.classList.add("dragging");
    }
    // Il riquadro resta dentro la pagina.
    const left = Math.min(Math.max(0, drag.left + dx), overlay.width - box.offsetWidth);
    const top = Math.min(Math.max(0, drag.top + dy), overlay.height - box.offsetHeight);
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
  };
  const end = (e) => {
    box.removeEventListener("pointermove", move);
    box.removeEventListener("pointerup", end);
    box.removeEventListener("pointercancel", end);
    if (!drag.moved) {
      if (doc.mode === "select") editObject(obj, box);
      return;
    }
    if (e.type === "pointercancel") {
      loadTextBoxes();
      return;
    }
    const [dx, dy] = pdfDelta(drag.left, drag.top, parseFloat(box.style.left), parseFloat(box.style.top));
    moveObject(obj, dx, dy);
  };
  box.addEventListener("pointermove", move);
  box.addEventListener("pointerup", end);
  box.addEventListener("pointercancel", end);
}

const ARROWS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

// Frecce sull'oggetto selezionato: 1 punto alla volta (10 con Maiusc); si applica quando si smette di premere.
export function nudgeSelection(event) {
  const sel = doc.selected;
  if (!sel || doc.mode !== "pointer" || !(event.key in ARROWS)) return false;
  if (doc.busy) return true; // mentre si applica uno spostamento le frecce non cambiano pagina
  const [ux, uy] = ARROWS[event.key];
  const step = (event.shiftKey ? 10 : 1) * doc.scale; // pixel per punto PDF
  const left = parseFloat(sel.box.style.left);
  const top = parseFloat(sel.box.style.top);
  sel.box.style.left = `${left + ux * step}px`;
  sel.box.style.top = `${top + uy * step}px`;
  sel.box.classList.add("dragging");
  if (!sel.start) sel.start = [left, top];
  clearTimeout(sel.timer);
  const armed = { page: doc.page, mode: doc.mode, version: doc.state.version };
  sel.timer = setTimeout(() => {
    // Pagina, modalità o documento cambiati nel frattempo: lo spostamento non si applica a un altro oggetto.
    if (doc.selected !== sel || doc.page !== armed.page || doc.mode !== armed.mode
        || doc.state.version !== armed.version) return;
    const [dx, dy] = pdfDelta(sel.start[0], sel.start[1], parseFloat(sel.box.style.left), parseFloat(sel.box.style.top));
    sel.start = null;
    moveObject(sel.obj, dx, dy);
  }, 450);
  return true;
}

export function clearSelection() {
  if (!doc.selected) return false;
  const pending = doc.selected.start;
  select(null);
  if (pending) loadTextBoxes(); // spostamento con le frecce annullato: il riquadro torna al suo posto
  message("");
  return true;
}

function setMode(mode) {
  doc.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  closePopover();
  message("");
  if (doc.viewport) loadTextBoxes();
}

function goTo(page) {
  if (!doc.pdf || page < 1 || page > doc.pdf.numPages) return;
  doc.page = page;
  drawPage();
}

function zoom(factor) {
  doc.scale = Math.min(4, Math.max(0.4, doc.scale * factor));
  drawPage();
}

export const pageNav = { next: () => goTo(doc.page + 1), prev: () => goTo(doc.page - 1) };

async function renderThumbs() {
  const run = ++doc.thumbsRun;
  const list = $("thumbs");
  list.innerHTML = "";
  const count = Math.min(doc.pdf.numPages, 300);
  for (let n = 1; n <= count; n++) {
    if (run !== doc.thumbsRun) return; // documento cambiato: si ricomincia
    const button = el("button", { class: "thumb", type: "button", "data-page": String(n), "aria-current": String(n === doc.page) });
    const canvas = el("canvas");
    button.append(canvas, document.createTextNode(String(n)));
    button.addEventListener("click", () => goTo(n));
    list.append(button);
    try {
      const page = await doc.pdf.getPage(n);
      const viewport = page.getViewport({ scale: 110 / page.getViewport({ scale: 1 }).width });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    } catch {
      // una miniatura che non si disegna non deve fermare le altre
    }
  }
}

// --- modifica e aggiunta di testo ---------------------------------------------------------------

function onOverlayClick(event) {
  if (doc.mode === "pointer") clearSelection(); // clic fuori dagli oggetti: niente selezionato
  if (doc.mode !== "add" || !doc.viewport) return;
  const rect = $("overlay").getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const [px, py] = doc.viewport.convertToPdfPoint(x, y);
  const marker = el("div", { class: "marker" });
  Object.assign(marker.style, { left: `${x}px`, top: `${y}px` });
  $("overlay").querySelectorAll(".marker").forEach((m) => m.remove());
  $("overlay").append(marker);
  openPopover({
    title: "Testo da aggiungere", text: "", extra: true, remove: false,
    note: "Il testo inizia dal punto scelto. Va a capo con Invio.",
    anchor: { left: event.clientX, top: event.clientY },
    onApply: (values) => apply("addtext", {
      page: doc.page, at: `${px.toFixed(2)},${py.toFixed(2)}`, text: values.text,
      size: values.size, color: values.color, bold: values.bold,
    }),
    onClose: () => marker.remove(),
  });
}

function editObject(obj, box) {
  box.classList.add("active");
  // Per indice, non per punto: con testi sovrapposti (apici, parole dentro una riga) il punto è ambiguo.
  const target = { page: doc.page, index: obj.index };
  const screen = box.getBoundingClientRect();
  openPopover({
    title: "Modifica testo", text: obj.text, extra: false, remove: true,
    note: obj.embedded
      ? "Font incorporato: se servono lettere che il file non contiene, il testo viene riscritto con un font di sistema."
      : "",
    anchor: { left: screen.left, top: screen.bottom },
    onApply: (values) => apply("edittext", { ...target, text: values.text.replace(/\n/g, " ") }),
    onDelete: () => apply("edittext", { ...target, text: " " }),
    onClose: () => box.classList.remove("active"),
  });
}

let popoverClose = null;

function openPopover({ title, text, extra, remove, note, anchor, onApply, onDelete, onClose }) {
  closePopover();
  const pop = $("popover");
  $("pop-title").textContent = title;
  $("pop-text").value = text;
  $("pop-extra").hidden = !extra;
  $("pop-delete").hidden = !remove;
  $("pop-note").textContent = note;
  pop.hidden = false;
  const width = 320;
  const left = Math.min(Math.max(8, anchor.left), window.innerWidth - width - 8);
  const top = Math.min(anchor.top + 8, window.innerHeight - pop.offsetHeight - 8);
  Object.assign(pop.style, { left: `${left}px`, top: `${Math.max(8, top)}px` });
  $("pop-text").focus();
  $("pop-apply").onclick = () => {
    const values = {
      text: $("pop-text").value, size: Number($("pop-size").value) || 11, color: $("pop-color").value,
      bold: $("pop-bold").checked,
    };
    if (!values.text.trim()) {
      toast("Scrivi un testo, oppure usa «Elimina»");
      return;
    }
    closePopover();
    onApply(values);
  };
  $("pop-delete").onclick = () => {
    closePopover();
    onDelete?.();
  };
  $("pop-text").onkeydown = (event) => {
    if (event.key === "Escape") closePopover();
    if (event.key === "Enter" && (event.ctrlKey || !extra)) {
      event.preventDefault();
      $("pop-apply").click();
    }
  };
  popoverClose = onClose;
}

function closePopover() {
  $("popover").hidden = true;
  if (popoverClose) popoverClose();
  popoverClose = null;
}

// --- operazioni sul documento ----------------------------------------------------------------------

function message(text, isError = false) {
  const box = $("doc-message");
  box.textContent = text;
  box.classList.toggle("error", isError);
}

export async function apply(tool, options, extra = {}) {
  if (!doc.state || doc.busy) return;
  doc.busy = true;
  message("Elaborazione…");
  try {
    const result = await post(`/api/doc/${doc.state.id}/apply`, { tool, options, ...extra });
    // Nel documento il nome del file intermedio non interessa: resta solo cosa è successo.
    const text = `${result.stdout || ""}${result.stderr ? `\n${result.stderr}` : ""}`
      .replace(/^wpdf: /gm, "").replace(/^[^\n:]+\.pdf: /gm, "").trim();
    const changed = result.document.version !== doc.state.version;
    message(text, result.code !== 0 && ![2, 3].includes(result.code));
    if (result.outputs?.length) showOutputs(result.outputs);
    if (changed) await load(result.document);
    else {
      doc.state = result.document;
      ctx.updateBar();
    }
  } catch (error) {
    message(error.message, true);
  } finally {
    doc.busy = false;
  }
}

function showOutputs(outputs) {
  const box = $("doc-message");
  for (const file of outputs) {
    const link = el("button", { class: "btn", type: "button" });
    link.textContent = `Scarica ${file.name}`;
    link.addEventListener("click", () => downloadFile(file.id, file.name).catch((e) => toast(e.message)));
    box.append(document.createElement("br"), link);
  }
}

async function deletePage() {
  if (!doc.pdf) return;
  if (doc.pdf.numPages === 1) {
    toast("È l'unica pagina del documento");
    return;
  }
  if (window.confirm(`Eliminare la pagina ${doc.page}?`)) await apply("pages", { delete: String(doc.page) });
}

async function step(action) {
  if (doc.busy) return; // un'operazione alla volta: due caricamenti insieme si annullerebbero a vicenda
  doc.busy = true;
  try {
    await load(await post(`/api/doc/${doc.state.id}/${action}`));
  } catch (error) {
    toast(error.message);
  } finally {
    doc.busy = false;
  }
}

export async function undo() {
  if (doc.state?.can_undo) await step("undo");
}

export async function redo() {
  if (doc.state?.can_redo) await step("redo");
}

// Vero se il documento è stato salvato (o, nel browser, scaricato).
export async function save() {
  if (!doc.state) return false;
  const pdfPath = doc.state.path && doc.state.path.toLowerCase().endsWith(".pdf");
  if (desktop() && pdfPath) { // un .p7m non si riscrive: la firma andrebbe persa
    try {
      doc.state = await post(`/api/doc/${doc.state.id}/save`);
      ctx.updateBar();
      toast(`Salvato in ${doc.state.path}`);
      return true;
    } catch (error) {
      toast(error.message);
      return false;
    }
  }
  return saveAs();
}

export async function saveAs() {
  if (!doc.state) return false;
  if (!desktop()) {
    try {
      await downloadFile(doc.state.file_id, doc.state.name);
    } catch (error) {
      toast(error.message);
      return false;
    }
    toast("Nel browser «Salva» scarica il file: per salvare direttamente sul disco usa la finestra di wmt-pdf");
    return true;
  }
  const path = await window.pywebview.api.save_dialog(doc.state.name);
  if (!path) return false;
  try {
    doc.state = await post(`/api/doc/${doc.state.id}/save`, { path });
    ctx.updateBar();
    toast(`Salvato in ${doc.state.path}`);
    return true;
  } catch (error) {
    toast(error.message);
    return false;
  }
}

// --- chiusura --------------------------------------------------------------------------------------

function askClose() {
  const dialog = $("close-dialog");
  $("cd-desc").textContent = `«${doc.state.name}» ha modifiche non salvate.`;
  return new Promise((resolve) => {
    const done = (choice) => {
      dialog.removeEventListener("cancel", onCancel);
      dialog.querySelectorAll("[data-choice]").forEach((b) => { b.onclick = null; });
      dialog.close();
      resolve(choice);
    };
    const onCancel = (event) => {
      event.preventDefault();
      done("cancel");
    };
    dialog.addEventListener("cancel", onCancel); // Esc
    dialog.querySelectorAll("[data-choice]").forEach((b) => { b.onclick = () => done(b.dataset.choice); });
    dialog.showModal();
    dialog.querySelector('[data-choice="save"]').focus();
  });
}

// Chiude il documento aperto; con modifiche non salvate chiede prima. Vero se è stato chiuso.
export async function closeDocument() {
  if (!doc.state || doc.busy) return false;
  if (doc.state.modified) {
    const choice = await askClose();
    if (choice === "cancel" || (choice === "save" && !(await save()))) return false;
  }
  const closing = doc.state;
  try {
    await post(`/api/doc/${closing.id}/close`);
  } catch {
    // già sparito dal server (es. riavviato): si chiude comunque la vista
  }
  closePopover();
  if (doc.task) doc.task.cancel();
  if (doc.loading) await doc.loading.destroy().catch(() => {});
  Object.assign(doc, { state: null, pdf: null, loading: null, viewport: null, page: 1, selected: null, reselect: null });
  doc.thumbsRun++; // ferma le miniature ancora in corso
  $("thumbs").innerHTML = "";
  $("overlay").innerHTML = "";
  message("");
  ctx.show("home");
  return true;
}

// --- unione con altri file -------------------------------------------------------------------------

let mergeFiles = [];

function chooseMergePosition(files) {
  mergeFiles = files;
  const list = $("md-files");
  list.innerHTML = "";
  for (const file of files) {
    const item = el("li");
    item.textContent = file.name;
    list.append(item);
  }
  $("merge-dialog").showModal();
}

async function runMerge() {
  const position = document.querySelector('input[name="md-position"]:checked').value;
  $("merge-dialog").close();
  const files = mergeFiles;
  mergeFiles = [];
  message(`Caricamento di ${files.length} file…`);
  let ids;
  try {
    ids = (await Promise.all(files.map((f) => upload(f)))).map((info) => info.id);
  } catch (error) {
    message(error.message, true);
    return;
  }
  await apply("merge", { bookmarks: false }, { files: ids, position });
}

export async function download() {
  if (doc.state) await downloadFile(doc.state.file_id, doc.state.name).catch((e) => toast(e.message));
}

// --- altri strumenti sul documento ----------------------------------------------------------------

function renderToolList() {
  const list = $("doc-tool-list");
  list.innerHTML = "";
  for (const tool of ctx.tools()) {
    if (tool.hidden || tool.max_files !== 1 || NOT_IN_LIST.has(tool.id)) continue;
    const button = el("button", { type: "button", style: `--tile:${tool.color}` },
      `<span class="app-icon">${svg(tool.icon)}</span>`);
    button.append(document.createTextNode(tool.label));
    button.title = tool.description;
    button.addEventListener("click", () => openToolDialog(tool));
    list.append(button);
  }
}

function openToolDialog(tool) {
  if (!doc.state) return;
  doc.dialogTool = tool;
  doc.dialogFiles = {};
  $("td-title").textContent = tool.label;
  $("td-desc").textContent = tool.description;
  renderOptions($("td-options"), tool, doc.dialogFiles, () => {});
  $("td-run").textContent = ["pdf", "fill", "signed"].includes(tool.output) ? "Applica al documento" : "Esegui";
  $("tool-dialog").showModal();
}

async function runDialogTool() {
  const tool = doc.dialogTool;
  const options = collectOptions($("td-options"), tool, doc.dialogFiles);
  const missing = missingOptions(tool, options);
  if (missing) {
    toast(missing);
    return;
  }
  $("tool-dialog").close();
  await apply(tool.id, options);
}
