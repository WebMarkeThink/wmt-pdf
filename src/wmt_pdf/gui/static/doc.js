// Vista documento: pagina con testo modificabile, miniature, strumenti, versioni e salvataggio.
import * as pdfjs from "/static/vendor/pdf.min.mjs";
import {
  $, api, collectOptions, desktop, downloadFile, el, fetchBlob, missingOptions, post, renderOptions, svg, toast,
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
  state: null, pdf: null, page: 1, scale: 1.25, mode: "select", viewport: null,
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
  if (doc.task) doc.task.cancel();
  const page = await doc.pdf.getPage(doc.page);
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
  doc.task = page.render({ canvasContext: context, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS });
  try {
    await doc.task.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
    return;
  } finally {
    doc.task = null;
  }
  $("doc-page").textContent = `${doc.page} / ${doc.pdf.numPages}`;
  $("doc-zoom").textContent = `${Math.round(doc.scale * 100)}%`;
  document.querySelectorAll(".thumb").forEach((t) => t.setAttribute("aria-current", String(Number(t.dataset.page) === doc.page)));
  await loadTextBoxes();
}

async function loadTextBoxes() {
  const overlay = $("overlay");
  overlay.innerHTML = "";
  overlay.className = `overlay ${doc.mode}`;
  let objects = [];
  try {
    objects = await api(`/api/doc/${doc.state.id}/textobjects?page=${doc.page}`);
  } catch {
    return; // pagina senza testo leggibile: si può comunque aggiungere testo
  }
  for (const obj of objects) {
    // PDF.js 6 converte solo punti: il riquadro si ricava dai due angoli.
    const [x0, y0] = doc.viewport.convertToViewportPoint(obj.bbox[0], obj.bbox[1]);
    const [x1, y1] = doc.viewport.convertToViewportPoint(obj.bbox[2], obj.bbox[3]);
    const box = el("div", { class: "tbox", title: obj.text });
    Object.assign(box.style, {
      left: `${Math.min(x0, x1) - 1}px`, top: `${Math.min(y0, y1) - 1}px`,
      width: `${Math.abs(x1 - x0) + 2}px`, height: `${Math.abs(y1 - y0) + 2}px`,
    });
    box.addEventListener("click", (event) => {
      if (doc.mode !== "select") return;
      event.stopPropagation();
      editObject(obj, box);
    });
    overlay.append(box);
  }
}

function setMode(mode) {
  doc.mode = mode;
  document.querySelectorAll(".mode").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  $("overlay").className = `overlay ${mode}`;
  closePopover();
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

export async function apply(tool, options) {
  if (!doc.state || doc.busy) return;
  doc.busy = true;
  message("Elaborazione…");
  try {
    const result = await post(`/api/doc/${doc.state.id}/apply`, { tool, options });
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

export async function save() {
  if (!doc.state) return;
  const pdfPath = doc.state.path && doc.state.path.toLowerCase().endsWith(".pdf");
  if (desktop() && pdfPath) { // un .p7m non si riscrive: la firma andrebbe persa
    try {
      doc.state = await post(`/api/doc/${doc.state.id}/save`);
      ctx.updateBar();
      toast(`Salvato in ${doc.state.path}`);
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  await saveAs();
}

export async function saveAs() {
  if (!doc.state) return;
  if (!desktop()) {
    await downloadFile(doc.state.file_id, doc.state.name);
    toast("Nel browser «Salva» scarica il file: per salvare direttamente sul disco usa la finestra di wmt-pdf");
    return;
  }
  const path = await window.pywebview.api.save_dialog(doc.state.name);
  if (!path) return;
  try {
    doc.state = await post(`/api/doc/${doc.state.id}/save`, { path });
    ctx.updateBar();
    toast(`Salvato in ${doc.state.path}`);
  } catch (error) {
    toast(error.message);
  }
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
