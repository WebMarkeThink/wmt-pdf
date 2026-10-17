// Funzioni condivise: API, file, icone, moduli delle opzioni, messaggi.

export const TOKEN = document.querySelector('meta[name="wpdf-token"]').content;
export const $ = (id) => document.getElementById(id);

// Icone a tratto, 24x24.
export const ICONS = {
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
  replace: '<path d="M4 7h10M4 12h7M4 17h5"/><path d="M15 13l3 3 3-3M18 8v8"/>',
  "edit-text": '<path d="M5 4h14M12 4v16M9 20h6"/>',
  "add-text": '<path d="M4 7V5h12v2M10 5v14M7 19h6"/><path d="M18 14v6M15 17h6"/>',
  form: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h4"/>',
  fill: '<rect x="4" y="3" width="13" height="18" rx="2"/><path d="M8 8h5M8 12h3"/><path d="m14 17 6-6 2 2-6 6h-2z"/>',
  "lock-form": '<rect x="4" y="3" width="13" height="18" rx="2"/><path d="M8 8h5"/><rect x="13" y="14" width="8" height="7" rx="1"/><path d="M15 14v-2a2 2 0 0 1 4 0v2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  unlock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2"/>',
  sign: '<path d="M3 17c3 0 4-8 6-8s1 7 3 7 2-4 4-4 2 3 5 3"/><path d="M3 21h18"/>',
  pen: '<path d="m15 4 5 5-10 10H5v-5z"/><path d="m13 6 5 5"/>',
};
export const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.info}</svg>`;

export async function api(path, options = {}) {
  const headers = { "X-Wpdf-Token": TOKEN, ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `errore ${response.status}`);
  return data;
}

export const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

export async function upload(file) {
  return api("/api/upload", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
}

export async function fetchBlob(id) {
  const response = await fetch(`/api/file/${id}`, { headers: { "X-Wpdf-Token": TOKEN } });
  if (!response.ok) throw new Error("file non disponibile");
  return response.blob();
}

export async function downloadFile(id, name) {
  const blob = await fetchBlob(id);
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

export function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), 4000);
}

export function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function el(tag, attrs = {}, html = "") {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  if (html) node.innerHTML = html;
  return node;
}

export function accepts(accept, name) {
  if (!accept || accept.includes("*")) return true;
  const lower = name.toLowerCase();
  return accept.some((ext) => lower.endsWith(ext));
}

// Finestra nativa (pywebview): finestre di dialogo vere per aprire e salvare.
export const desktop = () => Boolean(window.pywebview?.api?.open_dialog);

// --- moduli delle opzioni -----------------------------------------------------------------

// Disegna i campi delle opzioni di uno strumento in un <form>; skip: opzioni da non mostrare.
export function renderOptions(form, tool, files, onChange, skip = []) {
  form.innerHTML = "";
  const shown = tool.options.filter((o) => !skip.includes(o.name));
  if (!shown.length) form.append(el("p", { class: "muted small" }, "Nessuna opzione."));
  for (const opt of shown) {
    const id = `${form.id}-${opt.name}`;
    if (opt.kind === "bool") {
      const label = el("label", { class: "check" });
      const input = el("input", { type: "checkbox", id, name: opt.name });
      input.checked = opt.default === true;
      label.append(input, document.createTextNode(opt.label));
      form.append(label);
      continue;
    }
    const field = el("label", { class: "field", for: id });
    const caption = el("span", { class: opt.required ? "required" : "" });
    caption.textContent = opt.label;
    field.append(caption);
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
          files[opt.name] = info.id;
          chosen.textContent = info.name;
          onChange();
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
      input.addEventListener("input", onChange);
    }
    field.append(input);
    if (opt.help) {
      const help = el("small", { class: "muted" });
      help.textContent = opt.help;
      field.append(help);
    }
    form.append(field);
  }
  form.onchange = onChange;
}

export function collectOptions(form, tool, files, skip = []) {
  const options = {};
  for (const opt of tool.options) {
    if (skip.includes(opt.name)) continue;
    if (opt.kind === "file") {
      if (files[opt.name]) options[opt.name] = files[opt.name];
      continue;
    }
    const input = form.querySelector(`#${form.id}-${opt.name}`);
    if (!input) continue;
    if (opt.kind === "bool") options[opt.name] = input.checked;
    else if (input.value !== "") options[opt.name] = opt.kind === "number" ? Number(input.value) : input.value;
  }
  return options;
}

// Cosa manca per poter eseguire (stringa vuota se è tutto a posto).
export function missingOptions(tool, options, skip = []) {
  const absent = tool.options.filter((o) => !skip.includes(o.name) && o.required &&
    (options[o.name] === undefined || options[o.name] === ""));
  if (absent.length) return `Manca: ${absent.map((o) => o.label).join(", ")}`;
  if (tool.one_of.length && !tool.one_of.some((name) => options[name] !== undefined && options[name] !== "")) {
    const labels = tool.options.filter((o) => tool.one_of.includes(o.name)).map((o) => o.label.toLowerCase());
    return `Indica ${labels.join(" oppure ")}`;
  }
  return "";
}
