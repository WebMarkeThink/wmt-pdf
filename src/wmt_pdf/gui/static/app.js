// Interfaccia di wmt-pdf: barra dei file, schermata iniziale, strumenti, anteprima.
import {
  $, accepts, api, collectOptions, desktop, downloadFile, el, fetchBlob, lang, loadI18n, missingOptions, post,
  renderOptions, screenArea, size, svg, t, toast, upload,
} from "./common.js";
import {
  PDF_OPTIONS, clearSelection, closeDocument, currentDoc, download, initDocs, nudgeSelection, openDocument, pageNav,
  pdfjs, redo, reopenDocument, save, saveAs, undo,
} from "./doc.js";

const ICON_UP = '<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>';
const ICON_DOWN = '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>';
const ICON_X = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>';

const state = {
  tools: [], category: "tutti", tool: null, files: [], optionFiles: {}, session: 0, pending: 0, view: "home",
};

// --- viste ---------------------------------------------------------------------------------

function show(view) {
  state.view = view;
  for (const name of ["home", "tool", "doc"]) $(name).hidden = name !== view;
  document.querySelector(".main").classList.toggle("doc-mode", view === "doc");
  document.body.classList.toggle("doc-open", view === "doc");
  if (view === "home") {
    renderCategories();
    renderGrid();
    renderRecent();
  }
  updateBar();
}

function updateBar() {
  const doc = currentDoc();
  $("doc-tab").hidden = !doc;
  if (doc) {
    $("doc-name").textContent = doc.name;
    $("doc-name").title = doc.path || doc.name;
    $("doc-dirty").hidden = !doc.modified;
  }
  $("cmd-undo").disabled = !doc?.can_undo;
  $("cmd-redo").disabled = !doc?.can_redo;
  for (const id of ["cmd-save", "cmd-saveas", "cmd-download"]) $(id).disabled = !doc;
  document.title = doc ? `${doc.modified ? "• " : ""}${doc.name} - wmt-pdf` : "wmt-pdf";
}

// --- apertura e file recenti -----------------------------------------------------------------

async function openCommand() {
  if (desktop()) {
    const path = await window.pywebview.api.open_dialog();
    if (path) await openDocument({ path });
  } else {
    $("open-input").click();
  }
}

async function openUploaded(file) {
  if (!accepts([".pdf", ".p7m"], file.name)) {
    toast(t("{name}: per aprire serve un PDF; per convertire usa «Converti in PDF»", { name: file.name }));
    return;
  }
  try {
    const info = await upload(file);
    await openDocument({ file_id: info.id });
  } catch (error) {
    toast(error.message);
  }
}

async function recentFiles() {
  try {
    return await api("/api/recent");
  } catch {
    return { desktop: false, files: [] };
  }
}

function when(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(lang() === "it" ? "it-IT" : "en-GB", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

async function renderRecent() {
  const { files } = await recentFiles();
  const list = $("recent-list");
  list.innerHTML = "";
  $("recent-home").hidden = !files.length;
  for (const file of files.slice(0, 6)) {
    const button = el("button", { type: "button", title: file.path },
      '<span class="file-badge">PDF</span><span class="meta"><strong></strong><small></small></span>');
    button.querySelector("strong").textContent = file.name;
    button.querySelector("small").textContent = `${when(file.opened)} · ${file.path}`;
    button.addEventListener("click", () => openDocument({ path: file.path }));
    list.append(el("li")).append(button);
  }
}

async function toggleRecentMenu() {
  const menu = $("recent-menu");
  if (!menu.hidden) {
    closeMenu();
    return;
  }
  const { desktop: isDesktop, files } = await recentFiles();
  menu.innerHTML = "";
  if (!files.length) {
    const note = el("div", { class: "menu-empty" });
    note.textContent = isDesktop
      ? t("Nessun file recente.")
      : t("I file recenti sono disponibili nella finestra di wmt-pdf (wpdf gui), non nel browser.");
    menu.append(note);
  }
  for (const file of files) {
    const item = el("button", { type: "button", role: "menuitem" }, "<span></span><small></small>");
    item.querySelector("span").textContent = file.name;
    item.querySelector("small").textContent = file.path;
    item.addEventListener("click", () => {
      closeMenu();
      openDocument({ path: file.path });
    });
    menu.append(item);
  }
  menu.hidden = false;
  $("cmd-recent").setAttribute("aria-expanded", "true");
}

function closeMenu() {
  $("recent-menu").hidden = true;
  $("cmd-recent").setAttribute("aria-expanded", "false");
  $("compact-menu").hidden = true;
  $("cmd-compact").setAttribute("aria-expanded", "false");
}

// --- barra compatta (solo nella finestra di wmt-pdf) ----------------------------------------------------

function bindCompact() {
  const refresh = () => { $("compact-wrap").hidden = !window.pywebview?.api?.compact; };
  refresh();
  window.addEventListener("pywebviewready", refresh);
  $("cmd-compact").addEventListener("click", () => {
    const menu = $("compact-menu");
    const opening = menu.hidden;
    closeMenu();
    menu.hidden = !opening;
    $("cmd-compact").setAttribute("aria-expanded", String(opening));
  });
  $("compact-menu").querySelectorAll("[data-side]").forEach((item) => item.addEventListener("click", () => {
    closeMenu();
    window.pywebview?.api?.compact(item.dataset.side, screenArea());
  }));
}

// Comandi che arrivano dalla barra compatta quando si torna alla finestra completa.
window.wpdfExternal = async (action) => {
  if (action.command === "open") return openCommand();
  if (action.command === "recent") {
    if (state.view === "tool") show("home");
    return toggleRecentMenu();
  }
  if (action.open_file) return openDocument({ file_id: action.open_file });
  const tool = state.tools.find((t) => t.id === action.tool);
  if (!tool) return undefined;
  openTool(tool);
  for (const info of action.files || []) {
    if (state.files.length >= tool.max_files) break;
    if (accepts(tool.accept, info.name)) state.files.push(info);
    else toast(t("{name}: formato non adatto a {tool}", { name: info.name, tool: tool.label }));
  }
  renderFiles();
  return undefined;
};

// --- schermata iniziale --------------------------------------------------------------------------

function renderCategories() {
  const nav = $("categories");
  nav.innerHTML = "";
  const seen = new Map([["tutti", [t("Tutti gli strumenti"), "#667085"]]]);
  for (const tool of state.tools) if (!tool.hidden) seen.set(tool.category, [tool.category_label, tool.color]);
  for (const [id, [label, color]] of seen) {
    const button = el("button", { type: "button", "aria-current": String(id === state.category) },
      `<span class="dot" style="background:${color}"></span>${label}`);
    button.addEventListener("click", () => {
      state.category = id;
      show("home");
    });
    nav.append(button);
  }
}

function renderGrid() {
  const query = $("search").value.trim().toLowerCase();
  const grid = $("grid");
  grid.innerHTML = "";
  const visible = state.tools.filter((t) => !t.hidden &&
    (state.category === "tutti" || t.category === state.category) &&
    (!query || `${t.label} ${t.description} ${t.category_label}`.toLowerCase().includes(query)));
  for (const tool of visible) {
    const tile = el("button", { class: "tile", type: "button", role: "listitem", style: `--tile:${tool.color}` },
      `<span class="app-icon">${svg(tool.icon)}</span><strong>${tool.label}</strong><span class="desc">${tool.description}</span>`);
    tile.addEventListener("click", () => openTool(tool));
    grid.append(tile);
  }
  $("empty").hidden = visible.length > 0;
}

// --- strumento ---------------------------------------------------------------------------------------

function openTool(tool) {
  state.tool = tool;
  state.files = [];
  state.optionFiles = {};
  state.session++;
  state.pending = 0;
  $("result").hidden = true;
  const icon = $("tool-icon");
  icon.style.setProperty("--tile", tool.color);
  icon.innerHTML = svg(tool.icon);
  $("tool-name").textContent = tool.label;
  $("tool-desc").textContent = tool.description;
  $("file-input").multiple = tool.max_files > 1;
  const kinds = tool.accept.includes("*") ? t("qualsiasi formato supportato") : tool.accept.join(", ");
  $("drop-hint").textContent =
    t("oppure fai clic per sceglierli · {limit}{kinds}", {
      limit: tool.max_files > 1 ? t("fino a {n} file, ", { n: tool.max_files }) : "", kinds,
    });
  renderOptions($("options"), tool, state.optionFiles, updateRun);
  renderFiles();
  show("tool");
  window.scrollTo({ top: 0 });
}

function renderFiles() {
  const list = $("files");
  list.innerHTML = "";
  state.files.forEach((file, index) => {
    const item = el("li");
    const name = el("span", { class: "name", title: file.name });
    name.textContent = file.name;
    item.append(name, el("span", { class: "size" }, size(file.size)));
    if (state.tool.max_files > 1) {
      item.append(el("button", { class: "icon-btn", type: "button", "aria-label": t("Sposta su"), onclick: () => move(index, -1) }, ICON_UP));
      item.append(el("button", { class: "icon-btn", type: "button", "aria-label": t("Sposta giù"), onclick: () => move(index, 1) }, ICON_DOWN));
    }
    item.append(el("button", { class: "icon-btn", type: "button", "aria-label": t("Togli"), onclick: () => {
      state.files.splice(index, 1);
      renderFiles();
    } }, ICON_X));
    list.append(item);
  });
  updateRun();
}

function move(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.files.length) return;
  [state.files[index], state.files[target]] = [state.files[target], state.files[index]];
  renderFiles();
}

async function addFiles(fileList) {
  const tool = state.tool;
  const session = state.session;
  for (const file of fileList) {
    if (!accepts(tool.accept, file.name)) {
      toast(t("{name}: formato non adatto a {tool}", { name: file.name, tool: tool.label }));
      continue;
    }
    if (state.files.length + state.pending >= tool.max_files) {
      if (tool.max_files === 1 && !state.pending) state.files = [];
      else {
        toast(t("Al massimo {n} file", { n: tool.max_files }));
        break;
      }
    }
    state.pending++;
    try {
      const info = await upload(file);
      if (state.session !== session) return; // nel frattempo si è cambiato strumento
      state.files.push(info);
      renderFiles();
    } catch (error) {
      toast(`${file.name}: ${error.message}`);
    } finally {
      if (state.session === session) state.pending--;
    }
  }
}

function missing() {
  const tool = state.tool;
  if (state.files.length < tool.min_files) {
    return tool.min_files > 1 ? t("Servono almeno {n} file", { n: tool.min_files }) : t("Scegli un file");
  }
  return missingOptions(tool, collectOptions($("options"), tool, state.optionFiles));
}

function updateRun() {
  if (!state.tool) return;
  const reason = missing();
  $("run").disabled = Boolean(reason);
  $("run-hint").textContent = reason;
}

async function run() {
  const button = $("run");
  button.disabled = true;
  button.classList.add("busy");
  try {
    const result = await post("/api/run", {
      tool: state.tool.id, inputs: state.files.map((f) => f.id),
      options: collectOptions($("options"), state.tool, state.optionFiles),
    });
    showResult(result);
  } catch (error) {
    showResult({ code: 1, stdout: "", stderr: error.message, outputs: [] });
  } finally {
    button.classList.remove("busy");
    updateRun();
  }
}

function showResult(result) {
  $("result").hidden = false;
  const output = $("output");
  const text = (result.stdout || "") + (result.stderr ? `${result.stdout ? "\n" : ""}${result.stderr}` : "");
  output.textContent = text.replace(/^wpdf: /gm, "").trim();
  const signatureTool = ["verify", "p7m"].includes(state.tool.id);
  const failed = result.code !== 0 && !(signatureTool && [2, 3].includes(result.code));
  output.classList.toggle("error", failed);
  const badge = $("verdict");
  let [label, kind] = failed ? [t("Errore"), "bad"] : [t("Fatto"), "ok"];
  if (signatureTool) {
    const verdicts = { 0: [t("Firme valide"), "ok"], 2: [t("Non valida"), "bad"], 3: [t("Non verificabile"), "warn"] };
    [label, kind] = verdicts[result.code] || [label, kind];
  }
  badge.textContent = label;
  badge.className = `badge ${kind}`;
  badge.hidden = false;
  const list = $("outputs");
  list.innerHTML = "";
  for (const file of result.outputs) {
    const item = el("li");
    const name = el("span", { class: "name", title: file.name });
    name.textContent = file.name;
    item.append(name, el("span", { class: "muted small" }, size(file.size)));
    if (file.kind === "pdf") {
      item.append(el("button", { class: "btn", type: "button", onclick: () => preview(file) }, t("Anteprima")));
      item.append(el("button", { class: "btn", type: "button", onclick: () => openDocument({ file_id: file.id }) }, t("Apri")));
    }
    const download = el("button", { class: "btn", type: "button" }, t("Scarica"));
    download.addEventListener("click", () => downloadFile(file.id, file.name).catch((e) => toast(e.message)));
    item.append(download);
    list.append(item);
  }
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// --- anteprima dei risultati -----------------------------------------------------------------------

const viewer = { doc: null, page: 1, scale: 1.2, file: null, task: null };

async function preview(file) {
  try {
    const data = new Uint8Array(await (await fetchBlob(file.id)).arrayBuffer());
    if (viewer.loading) await viewer.loading.destroy();
    viewer.loading = pdfjs.getDocument({ data, ...PDF_OPTIONS });
    viewer.doc = await viewer.loading.promise;
    viewer.page = 1;
    viewer.file = file;
    $("viewer-name").textContent = file.name;
    $("viewer").showModal();
    await draw();
  } catch (error) {
    toast(t("Anteprima non disponibile: {error}", { error: error.message }));
  }
}

async function draw() {
  if (viewer.task) viewer.task.cancel();
  const page = await viewer.doc.getPage(viewer.page);
  const ratio = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: viewer.scale });
  const canvas = $("canvas");
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  viewer.task = page.render({ canvasContext: context, viewport, annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS });
  try {
    await viewer.task.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
  } finally {
    viewer.task = null;
  }
  $("page-label").textContent = `${viewer.page} / ${viewer.doc.numPages}`;
}

function bindViewer() {
  $("prev").addEventListener("click", () => viewer.page > 1 && (viewer.page--, draw()));
  $("next").addEventListener("click", () => viewer.page < viewer.doc.numPages && (viewer.page++, draw()));
  $("zoom-in").addEventListener("click", () => ((viewer.scale = Math.min(4, viewer.scale * 1.2)), draw()));
  $("zoom-out").addEventListener("click", () => ((viewer.scale = Math.max(0.4, viewer.scale / 1.2)), draw()));
  $("viewer-close").addEventListener("click", () => $("viewer").close());
  $("viewer-save").addEventListener("click", (event) => {
    event.preventDefault();
    downloadFile(viewer.file.id, viewer.file.name).catch((e) => toast(e.message));
  });
}

// --- comandi, trascinamento e tastiera ------------------------------------------------------------

function bindCommands() {
  $("cmd-open").addEventListener("click", openCommand);
  $("cmd-recent").addEventListener("click", toggleRecentMenu);
  $("cmd-undo").addEventListener("click", undo);
  $("cmd-redo").addEventListener("click", redo);
  $("cmd-save").addEventListener("click", save);
  $("cmd-saveas").addEventListener("click", saveAs);
  $("cmd-download").addEventListener("click", download);
  $("doc-name").addEventListener("click", () => show("doc"));
  $("doc-close").addEventListener("click", closeDocument);
  $("cmd-merge").addEventListener("click", () => {
    const merge = state.tools.find((t) => t.id === "merge");
    if (merge) openTool(merge);
  });
  $("brand").addEventListener("click", () => {
    state.category = "tutti";
    show("home");
  });
  $("open-input").addEventListener("change", () => {
    const file = $("open-input").files[0];
    $("open-input").value = "";
    if (file) openUploaded(file);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".menu-wrap")) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    const mod = event.ctrlKey || event.metaKey;
    // Con una finestra di dialogo aperta le frecce e le scorciatoie di modifica restano a lei.
    const typing = event.target.closest("input, textarea, select") || document.querySelector("dialog[open]");
    const key = event.key.toLowerCase();
    if (mod && key === "o") {
      event.preventDefault();
      openCommand();
    } else if (mod && key === "s") {
      event.preventDefault();
      if (event.shiftKey) saveAs();
      else save();
    } else if (mod && key === "w" && currentDoc()) {
      event.preventDefault();
      closeDocument();
    } else if (mod && !typing && key === "z") {
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    } else if (mod && !typing && key === "a" && state.view === "doc") {
      event.preventDefault(); // «seleziona tutto» sulla pagina evidenzierebbe tutto in scuro
    } else if (mod && !typing && key === "y") {
      event.preventDefault();
      redo();
    } else if (!typing && state.view === "doc" && nudgeSelection(event)) {
      event.preventDefault(); // le frecce spostano l'oggetto selezionato invece di cambiare pagina
    } else if (!typing && state.view === "doc" && event.key === "Escape" && clearSelection()) {
      event.preventDefault();
    } else if (!typing && state.view === "doc" && ["PageDown", "ArrowRight"].includes(event.key)) {
      pageNav.next();
    } else if (!typing && state.view === "doc" && ["PageUp", "ArrowLeft"].includes(event.key)) {
      pageNav.prev();
    } else if (event.key === "Escape") {
      closeMenu();
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (currentDoc()?.modified) event.preventDefault(); // il browser chiede conferma prima di perdere le modifiche
  });
}

function bindDrop() {
  const drop = $("drop");
  const input = $("file-input");
  drop.addEventListener("keydown", (event) => (event.key === "Enter" || event.key === " ") && input.click());
  input.addEventListener("change", () => {
    addFiles([...input.files]);
    input.value = "";
  });
  for (const name of ["dragenter", "dragover"]) {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
  }
  for (const name of ["dragleave", "drop"]) drop.addEventListener(name, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    addFiles([...event.dataTransfer.files]);
  });
  // Un PDF trascinato fuori dalla zona dei file si apre come documento.
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    event.preventDefault(); // sempre: altrimenti il browser apre il file e si perde la sessione
    if (state.view === "tool") return;
    const file = event.dataTransfer.files[0];
    if (file) openUploaded(file);
  });
}

// --- lingua -------------------------------------------------------------------------------------------

const REOPEN = "wpdf-reopen";

function bindLanguage() {
  const select = $("language");
  select.value = lang();
  select.addEventListener("change", async () => {
    try {
      await post("/api/settings", { language: select.value });
    } catch (error) {
      toast(error.message);
      select.value = lang();
      return;
    }
    // La pagina si ricarica nella nuova lingua; il documento aperto (con le sue versioni) resta sul server.
    const doc = currentDoc();
    try {
      if (doc) sessionStorage.setItem(REOPEN, doc.id);
    } catch {
      // senza sessionStorage il documento si riapre dai recenti
    }
    window.location.reload();
  });
}

async function reopenAfterReload() {
  let id = null;
  try {
    id = sessionStorage.getItem(REOPEN);
    sessionStorage.removeItem(REOPEN);
  } catch {
    return;
  }
  if (id) await reopenDocument(id).catch(() => {});
}

async function start() {
  await loadI18n();
  state.tools = await api("/api/tools");
  initDocs({ show, updateBar, tools: () => state.tools });
  $("search").addEventListener("input", renderGrid);
  $("back").addEventListener("click", () => show("home"));
  $("run").addEventListener("click", run);
  bindCommands();
  bindCompact();
  bindDrop();
  bindViewer();
  bindLanguage();
  show("home");
  await reopenAfterReload();
}

start().catch((error) => toast(t("Avvio non riuscito: {error}", { error: error.message })));
