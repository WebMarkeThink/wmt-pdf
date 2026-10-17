// Interfaccia di wmt-pdf: griglia di strumenti, file, opzioni, risultati e anteprima.
import * as pdfjs from "/static/vendor/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.mjs";

const TOKEN = document.querySelector('meta[name="wpdf-token"]').content;
const $ = (id) => document.getElementById(id);

// Icone a tratto, 24x24.
const ICONS = {
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
  "shield-check": '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  envelope: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  comment: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
  merge: '<path d="M7 3h6l3 3v6"/><path d="M4 8h6l3 3v10H4z"/><path d="M17 15l3 3-3 3M13 18h7"/>',
  split: '<path d="M5 3h9l4 4v4"/><path d="M5 3v18h13v-4"/><path d="M3 14h18" stroke-dasharray="2 2"/>',
  pages: '<rect x="7" y="3" width="12" height="15" rx="1.5"/><path d="M5 7v12a2 2 0 0 0 2 2h9"/>',
  rotate: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  convert: '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
  export: '<path d="M6 3h8l4 4v5"/><path d="M6 3v18h7"/><path d="M15 18h6M18 15l3 3-3 3"/>',
  ocr: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M8 9h8M8 12h8M8 15h5"/>',
  compress: '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7"/>',
  watermark: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  eraser: '<path d="M8 20h12"/><path d="M5 15 15 5l5 5-8.5 8.5H8z"/><path d="m10 10 5 5"/>',
  highlight: '<path d="M9 11 4 16v4h4l5-5"/><path d="m9 11 4-4 4 4-4 4z"/><path d="M14 20h7"/>',
  form: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h4"/>',
  fill: '<rect x="4" y="3" width="13" height="18" rx="2"/><path d="M8 8h5M8 12h3"/><path d="m14 17 6-6 2 2-6 6h-2z"/>',
  "lock-form": '<rect x="4" y="3" width="13" height="18" rx="2"/><path d="M8 8h5"/><rect x="13" y="14" width="8" height="7" rx="1"/><path d="M15 14v-2a2 2 0 0 1 4 0v2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  unlock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2"/>',
  sign: '<path d="M3 17c3 0 4-8 6-8s1 7 3 7 2-4 4-4 2 3 5 3"/><path d="M3 21h18"/>',
  pen: '<path d="m15 4 5 5-10 10H5v-5z"/><path d="m13 6 5 5"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.info}</svg>`;
const ICON_UP = '<svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg>';
const ICON_DOWN = '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>';
const ICON_X = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>';

const state = { tools: [], category: "tutti", tool: null, files: [], optionFiles: {}, session: 0, pending: 0 };

// --- API -------------------------------------------------------------------------------

async function api(path, options = {}) {
  const headers = { "X-Wpdf-Token": TOKEN, ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `errore ${response.status}`);
  return data;
}

async function upload(file) {
  return api("/api/upload", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
}

const fileUrl = (id, download = false) => `/api/file/${id}${download ? "?download=1" : ""}`;

async function fetchBlob(id) {
  const response = await fetch(fileUrl(id), { headers: { "X-Wpdf-Token": TOKEN } });
  if (!response.ok) throw new Error("file non disponibile");
  return response.blob();
}

// --- utilità ---------------------------------------------------------------------------

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function el(tag, attrs = {}, html = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  if (html) node.innerHTML = html;
  return node;
}

function accepts(accept, name) {
  if (!accept || accept.includes("*")) return true;
  const lower = name.toLowerCase();
  return accept.some((ext) => lower.endsWith(ext));
}

// --- home ------------------------------------------------------------------------------

function renderCategories() {
  const nav = $("categories");
  nav.innerHTML = "";
  const seen = new Map([["tutti", ["Tutti gli strumenti", "#667085"]]]);
  for (const tool of state.tools) seen.set(tool.category, [tool.category_label, tool.color]);
  for (const [id, [label, color]] of seen) {
    const button = el("button", { type: "button", "aria-current": String(id === state.category) },
      `<span class="dot" style="background:${color}"></span>${label}`);
    button.addEventListener("click", () => {
      state.category = id;
      showHome();
    });
    nav.append(button);
  }
}

function renderGrid() {
  const query = $("search").value.trim().toLowerCase();
  const grid = $("grid");
  grid.innerHTML = "";
  const visible = state.tools.filter((t) =>
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

function showHome() {
  state.tool = null;
  $("tool").hidden = true;
  $("home").hidden = false;
  renderCategories();
  renderGrid();
}

// --- strumento -----------------------------------------------------------------------------

function openTool(tool, initialFiles = []) {
  state.tool = tool;
  state.files = [...initialFiles];
  state.optionFiles = {};
  state.session++;
  state.pending = 0;
  $("home").hidden = true;
  $("tool").hidden = false;
  $("result").hidden = true;
  const icon = $("tool-icon");
  icon.style.setProperty("--tile", tool.color);
  icon.innerHTML = svg(tool.icon);
  $("tool-name").textContent = tool.label;
  $("tool-desc").textContent = tool.description;
  $("file-input").multiple = tool.max_files > 1;
  const kinds = tool.accept.includes("*") ? "qualsiasi formato supportato" : tool.accept.join(", ");
  $("drop-hint").textContent = `oppure fai clic per sceglierli · ${tool.max_files > 1 ? `fino a ${tool.max_files} file, ` : ""}${kinds}`;
  renderOptions();
  renderFiles();
  window.scrollTo({ top: 0 });
}

function renderFiles() {
  const list = $("files");
  list.innerHTML = "";
  state.files.forEach((file, index) => {
    const item = el("li");
    item.append(el("span", { class: "name", title: file.name }, ""));
    item.querySelector(".name").textContent = file.name;
    item.append(el("span", { class: "size" }, size(file.size)));
    if (state.tool.max_files > 1) {
      item.append(el("button", { class: "icon-btn", type: "button", "aria-label": "Sposta su", onclick: () => move(index, -1) }, ICON_UP));
      item.append(el("button", { class: "icon-btn", type: "button", "aria-label": "Sposta giù", onclick: () => move(index, 1) }, ICON_DOWN));
    }
    item.append(el("button", { class: "icon-btn", type: "button", "aria-label": "Togli", onclick: () => {
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
      toast(`${file.name}: formato non adatto a ${tool.label}`);
      continue;
    }
    if (state.files.length + state.pending >= tool.max_files) {
      if (tool.max_files === 1 && !state.pending) state.files = [];
      else {
        toast(`Al massimo ${tool.max_files} file`);
        break;
      }
    }
    state.pending++;
    try {
      const info = await upload(file);
      // Se nel frattempo si è cambiato strumento, il file non appartiene più a questa schermata.
      if (state.session !== session) return;
      state.files.push(info);
      renderFiles();
    } catch (error) {
      toast(`${file.name}: ${error.message}`);
    } finally {
      if (state.session === session) state.pending--;
    }
  }
}

function renderOptions() {
  const form = $("options");
  form.innerHTML = "";
  if (!state.tool.options.length) {
    form.append(el("p", { class: "muted small" }, "Nessuna opzione: basta scegliere il file."));
  }
  for (const opt of state.tool.options) {
    const id = `opt-${opt.name}`;
    if (opt.kind === "bool") {
      const label = el("label", { class: "check" });
      const input = el("input", { type: "checkbox", id, name: opt.name });
      input.checked = opt.default === true;
      label.append(input, document.createTextNode(opt.label));
      form.append(label);
      continue;
    }
    const field = el("label", { class: "field", for: id });
    field.append(el("span", { class: opt.required ? "required" : "" }, ""));
    field.querySelector("span").textContent = opt.label;
    let input;
    if (opt.kind === "choice") {
      input = el("select", { id, name: opt.name });
      if (!opt.required && opt.default == null) input.append(el("option", { value: "" }, "Automatico"));
      opt.choices.forEach((choice, i) => {
        const option = el("option", { value: choice });
        option.textContent = opt.labels[i] || choice;
        if (choice === opt.default) option.selected = true;
        input.append(option);
      });
    } else if (opt.kind === "file") {
      input = el("div", { class: "file-option" });
      const picker = el("input", { type: "file", id, hidden: "", accept: opt.accept.join(",") });
      const button = el("button", { class: "btn", type: "button" }, "Scegli…");
      const chosen = el("span", { class: "chosen" }, "nessun file");
      button.addEventListener("click", () => picker.click());
      picker.addEventListener("change", async () => {
        const file = picker.files[0];
        if (!file) return;
        try {
          const info = await upload(file);
          state.optionFiles[opt.name] = info.id;
          chosen.textContent = info.name;
          updateRun();
        } catch (error) {
          toast(error.message);
        }
      });
      input.append(button, chosen, picker);
    } else {
      const type = opt.kind === "password" ? "password" : opt.kind === "number" ? "number" : "text";
      input = el("input", { type, id, name: opt.name });
      if (opt.kind === "number") input.step = "any";
      if (opt.default != null) input.value = opt.default;
      input.addEventListener("input", updateRun);
    }
    field.append(input);
    if (opt.help) field.append(el("small", { class: "muted" }, opt.help));
    form.append(field);
  }
}

function collectOptions() {
  const options = {};
  for (const opt of state.tool.options) {
    if (opt.kind === "file") {
      if (state.optionFiles[opt.name]) options[opt.name] = state.optionFiles[opt.name];
      continue;
    }
    const input = $(`opt-${opt.name}`);
    if (opt.kind === "bool") options[opt.name] = input.checked;
    else if (input.value !== "") options[opt.name] = opt.kind === "number" ? Number(input.value) : input.value;
  }
  return options;
}

function missing() {
  const tool = state.tool;
  if (state.files.length < tool.min_files) {
    return tool.min_files > 1 ? `Servono almeno ${tool.min_files} file` : "Scegli un file";
  }
  const options = collectOptions();
  const absent = tool.options.filter((o) => o.required && (options[o.name] === undefined || options[o.name] === ""));
  if (absent.length) return `Manca: ${absent.map((o) => o.label).join(", ")}`;
  if (tool.one_of.length && !tool.one_of.some((name) => options[name] !== undefined && options[name] !== "")) {
    const labels = tool.options.filter((o) => tool.one_of.includes(o.name)).map((o) => o.label);
    return `Indica ${labels.map((l) => l.toLowerCase()).join(" oppure ")}`;
  }
  return "";
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
    const result = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: state.tool.id, inputs: state.files.map((f) => f.id), options: collectOptions() }),
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
  let [label, kind] = failed ? ["Errore", "bad"] : ["Fatto", "ok"];
  if (signatureTool) {
    const verdicts = { 0: ["Firme valide", "ok"], 2: ["Non valida", "bad"], 3: ["Non verificabile", "warn"] };
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
      item.append(el("button", { class: "btn", type: "button", onclick: () => preview(file) }, "Anteprima"));
    }
    const save = el("a", { class: "btn", href: "#", download: file.name }, "Salva");
    save.addEventListener("click", (event) => download(event, file));
    item.append(save);
    list.append(item);
  }
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function download(event, file) {
  event.preventDefault();
  try {
    const blob = await fetchBlob(file.id);
    const url = URL.createObjectURL(blob);
    const link = el("a", { href: url, download: file.name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (error) {
    toast(error.message);
  }
}

// --- anteprima -------------------------------------------------------------------------------

const viewer = { doc: null, page: 1, scale: 1.2, file: null, task: null };
const VENDOR = "/static/vendor/";

async function preview(file) {
  try {
    const data = new Uint8Array(await (await fetchBlob(file.id)).arrayBuffer());
    if (viewer.doc) await viewer.doc.destroy();
    viewer.doc = await pdfjs.getDocument({
      data,
      isEvalSupported: false,
      enableScripting: false,
      wasmUrl: `${VENDOR}wasm/`, // decodificatori delle immagini delle scansioni (JBIG2, JPEG 2000)
      standardFontDataUrl: `${VENDOR}standard_fonts/`,
      cMapUrl: `${VENDOR}cmaps/`,
      cMapPacked: true,
    }).promise;
    viewer.page = 1;
    viewer.file = file;
    $("viewer-name").textContent = file.name;
    $("viewer").showModal();
    await draw();
  } catch (error) {
    toast(`Anteprima non disponibile: ${error.message}`);
  }
}

async function draw() {
  if (viewer.task) viewer.task.cancel(); // un clic veloce non deve sovrapporre due disegni
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
  $("viewer-save").addEventListener("click", (event) => download(event, viewer.file));
  $("viewer").addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") $("next").click();
    if (event.key === "ArrowLeft") $("prev").click();
  });
}

// --- avvio ---------------------------------------------------------------------------------------

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
    addFiles([...event.dataTransfer.files]);
  });
  // Un PDF trascinato sulla schermata iniziale si apre con «Informazioni».
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", async (event) => {
    event.preventDefault(); // sempre: altrimenti il browser apre il file e si perde la sessione
    if ($("home").hidden) return;
    const files = [...event.dataTransfer.files];
    if (!files.length) return;
    const info = state.tools.find((t) => t.id === "info");
    openTool(info);
    await addFiles(files.slice(0, 1));
  });
}

async function start() {
  state.tools = await api("/api/tools");
  $("search").addEventListener("input", renderGrid);
  $("back").addEventListener("click", showHome);
  $("run").addEventListener("click", run);
  $("options").addEventListener("change", updateRun);
  bindDrop();
  bindViewer();
  showHome();
}

start().catch((error) => toast(`Avvio non riuscito: ${error.message}`));
