// Barra compatta: comandi rapidi; i file trascinati si caricano qui e si aprono nella finestra completa.
import { $, accepts, api, el, loadI18n, screenArea, svg, t, upload } from "./common.js";

const SIDES = ["top", "right", "bottom", "left"];
const SIDE_NAMES = { top: "in alto", right: "a destra", bottom: "in basso", left: "a sinistra" };
const QUICK = ["merge", "convert", "optimize", "ocr", "sign"]; // strumenti nella barra
const OPEN_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/>'
  + '<path d="M3 7v11a2 2 0 0 0 2 2h13l3-9H7l-3 8"/></svg>';
const RECENT_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';

let side = new URLSearchParams(location.search).get("side") || "top";
let tools = [];

const desktopApi = () => window.pywebview?.api;

function expand(action) {
  desktopApi()?.expand(action ?? null);
}

function setSide(value) {
  side = SIDES.includes(value) ? value : "top";
  $("compact").dataset.side = side;
  const next = SIDES[(SIDES.indexOf(side) + 1) % SIDES.length];
  const button = $("cycle-side");
  button.title = t("Sposta la barra {side}", { side: t(SIDE_NAMES[next]) });
  const arrows = { top: "M12 19V5M6 11l6-6 6 6", right: "M5 12h14M13 6l6 6-6 6", bottom: "M12 5v14M6 13l6 6 6-6",
    left: "M19 12H5M11 6l-6 6 6 6" };
  button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${arrows[next]}"/></svg>`;
  try {
    localStorage.setItem("wpdf-compact-side", side);
  } catch {
    // memoria del browser non disponibile: si riparte dal lato in alto
  }
}

function button(icon, title, onClick, toolId) {
  const b = el("button", { class: "cbtn", type: "button", title });
  b.innerHTML = icon;
  b.addEventListener("click", onClick);
  if (toolId) b.dataset.tool = toolId;
  return b;
}

function render() {
  const actions = $("actions");
  actions.append(
    button(OPEN_ICON, t("Apri un PDF"), () => expand({ command: "open" })),
    button(RECENT_ICON, t("Recenti"), () => expand({ command: "recent" })),
  );
  for (const id of QUICK) {
    const tool = tools.find((t) => t.id === id);
    if (!tool) continue;
    const b = button(svg(tool.icon), t("{tool}: fai clic o trascina qui i file", { tool: tool.label }), () => expand({ tool: id }), id);
    b.style.color = tool.color;
    actions.append(b);
  }
}

// --- file trascinati ------------------------------------------------------------------------------

function note(text) {
  const box = $("drop-note");
  box.hidden = !text;
  box.textContent = text || "";
}

async function dropped(files, toolId) {
  if (!files.length) return;
  note(files.length === 1 ? t("Caricamento…") : t("Caricamento di {n} file…", { n: files.length }));
  let infos;
  try {
    infos = await Promise.all(files.map((f) => upload(f)));
  } catch (error) {
    note(error.message);
    setTimeout(() => note(""), 3000);
    return;
  }
  note("");
  const single = infos.length === 1 && accepts([".pdf", ".p7m"], infos[0].name);
  if (toolId) expand({ tool: toolId, files: infos });
  else if (single) expand({ open_file: infos[0].id });
  else expand({ tool: "merge", files: infos }); // più file sulla barra: si uniscono
}

function bindDrop() {
  const bar = $("compact");
  document.addEventListener("dragover", (event) => {
    event.preventDefault();
    bar.classList.add("over");
    document.querySelectorAll(".cbtn.over").forEach((b) => b.classList.remove("over"));
    event.target.closest?.(".cbtn[data-tool]")?.classList.add("over");
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) {
      bar.classList.remove("over");
      document.querySelectorAll(".cbtn.over").forEach((b) => b.classList.remove("over"));
    }
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    bar.classList.remove("over");
    document.querySelectorAll(".cbtn.over").forEach((b) => b.classList.remove("over"));
    const target = event.target.closest?.(".cbtn[data-tool]");
    dropped([...event.dataTransfer.files], target?.dataset.tool);
  });
}

async function start() {
  await loadI18n();
  setSide(side);
  $("expand").addEventListener("click", () => expand());
  $("cycle-side").addEventListener("click", async () => {
    const next = SIDES[(SIDES.indexOf(side) + 1) % SIDES.length];
    const result = await desktopApi()?.dock(next, screenArea());
    if (result) setSide(result);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") expand();
  });
  bindDrop();
  try {
    tools = await api("/api/tools");
  } catch {
    tools = [];
  }
  render();
}

start();
