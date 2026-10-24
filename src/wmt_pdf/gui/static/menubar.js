// Menu classico in cima alla finestra: File, Modifica, Visualizza, Aiuto. Ogni voce richiama un comando che esiste
// già (barra, pannelli, scorciatoie): il menu li raccoglie dove li si cerca, con le scorciatoie scritte accanto.
import { $, api, el, t, toast } from "./common.js";
import { apply, closeDocument, currentDoc, download, openDocument, redo, save, saveAs, undo } from "./doc.js";
import { organizeDocument } from "./organize.js";
import { printDocument } from "./print.js";

const PROJECT_PAGE = "https://github.com/WebMarkeThink/wmt-pdf";
let ctx = null; // { openCommand, recentFiles, mergeFiles, openTool, tools }
let openTitle = null; // pulsante del menu aperto

const desktop = () => Boolean(window.pywebview?.api);
const hasDoc = () => Boolean(currentDoc());

// Una voce: { label, keys, run, enabled, checked, submenu } oppure "-" per una riga di separazione.
function menus() {
  return [
    { title: t("File"), items: () => [
      { label: t("Apri…"), keys: "Ctrl+O", run: () => ctx.openCommand() },
      { label: t("Recenti"), submenu: recentItems },
      { label: t("Unisci file…"), run: () => ctx.mergeFiles() },
      "-",
      { label: t("Salva"), keys: "Ctrl+S", run: save, enabled: hasDoc },
      { label: t("Salva con nome…"), keys: "Ctrl+Maiusc+S", run: saveAs, enabled: hasDoc },
      { label: t("Scarica una copia"), run: download, enabled: hasDoc },
      { label: t("Esporta"), submenu: exportItems, enabled: hasDoc },
      "-",
      { label: t("Stampa…"), keys: "Ctrl+P", run: printDocument, enabled: hasDoc },
      "-",
      { label: t("Chiudi documento"), keys: "Ctrl+W", run: closeDocument, enabled: hasDoc },
    ] },
    { title: t("Modifica"), items: () => [
      { label: t("Annulla"), keys: "Ctrl+Z", run: undo, enabled: () => Boolean(currentDoc()?.can_undo) },
      { label: t("Ripeti"), keys: "Ctrl+Y", run: redo, enabled: () => Boolean(currentDoc()?.can_redo) },
      "-",
      { label: t("Cerca e sostituisci…"), run: () => $("find-text").focus(), enabled: hasDoc },
      { label: t("Organizza pagine…"), run: () => organizeDocument(), enabled: hasDoc },
      { label: t("Prepara modulo…"), run: () => $("prep-open").click(), enabled: hasDoc },
      "-",
      { label: t("Tutti gli strumenti"), run: () => ctx.home() },
    ] },
    { title: t("Visualizza"), items: () => [
      { label: t("Ingrandisci"), keys: "+", run: () => $("doc-zoom-in").click(), enabled: hasDoc },
      { label: t("Riduci"), keys: "−", run: () => $("doc-zoom-out").click(), enabled: hasDoc },
      "-",
      { label: t("Barra compatta"), submenu: compactItems, enabled: () => Boolean(window.pywebview?.api?.compact) },
      { label: t("Lingua"), submenu: languageItems },
    ] },
    { title: t("Aiuto"), items: () => [
      { label: t("Informazioni su wmt-pdf"), run: about },
      { label: t("Licenza"), submenu: () => [
        { label: t("Licenza di wmt-pdf (AGPL-3.0)"), run: () => showLicense("license") },
        { label: t("Licenze dei componenti di terze parti"), run: () => showLicense("third_party") },
        { label: t("Codice sorgente su GitHub"), run: () => openPage(PROJECT_PAGE) },
      ] },
      { label: t("Aggiornamenti"), submenu: () => [
        { label: t("Cerca aggiornamenti ora"), run: checkNow },
        { label: t("Controlla automaticamente ogni giorno"), checked: () => $("update-check").checked,
          run: () => $("update-check").click() },
        { label: t("Tutte le versioni"), run: () => openPage(`${PROJECT_PAGE}/releases`) },
      ] },
      { label: t("App predefinita per i PDF…"), run: () => $("default-app").click(),
        enabled: () => !$("default-app").hidden },
      { label: t("Segnala un problema"), run: () => openPage(`${PROJECT_PAGE}/issues`) },
    ] },
  ];
}

async function recentItems() {
  const { files } = await ctx.recentFiles();
  if (!files.length) return [{ label: desktop() ? t("Nessun file recente.") : t("Solo nella finestra di wmt-pdf"),
    enabled: () => false }];
  return files.slice(0, 12).map((file) => ({ label: file.name, hint: file.path,
    run: () => openDocument({ path: file.path }) }));
}

function exportItems() {
  const tool = ctx.tools().find((item) => item.id === "export");
  const option = tool?.options.find((o) => o.name === "to");
  if (!option) return [];
  return option.choices.map((value, i) => ({
    label: t(option.labels[i] || value),
    run: () => apply("export", { to: value, dpi: 150 }),
  }));
}

function compactItems() {
  const sides = [["top", t("In alto")], ["bottom", t("In basso")], ["left", t("A sinistra")], ["right", t("A destra")]];
  return sides.map(([side, label]) => ({ label, run: () => $("compact-menu").querySelector(`[data-side="${side}"]`).click() }));
}

function languageItems() {
  const select = $("language");
  return [...select.options].map((option) => ({
    label: option.textContent, checked: () => select.value === option.value,
    run: () => {
      if (select.value === option.value) return;
      select.value = option.value;
      select.dispatchEvent(new Event("change"));
    },
  }));
}

// --- Aiuto -----------------------------------------------------------------------------------------------------------

function openPage(url) {
  if (window.pywebview?.api?.open_url) window.pywebview.api.open_url(url);
  else window.open(url, "_blank", "noopener");
}

function infoDialog(title, body, { text = null, action = null } = {}) {
  $("info-title").textContent = title;
  $("info-body").textContent = body;
  $("info-text").hidden = text === null;
  $("info-text").textContent = text ?? "";
  $("info-action").hidden = !action;
  if (action) {
    $("info-action").textContent = action.label;
    $("info-action").onclick = action.run;
  }
  $("info-dialog").showModal();
}

async function about() {
  let info;
  try {
    info = await api("/api/about");
  } catch (error) {
    toast(error.message);
    return;
  }
  infoDialog("wmt-pdf", t("Versione {version} · licenza {license}. Programma libero: puoi usarlo, studiarlo, modificarlo e ridistribuirlo alle condizioni della licenza. I file restano su questo computer.",
    { version: info.version, license: info.license }),
  { action: { label: t("Codice sorgente su GitHub"), run: () => openPage(info.project) } });
}

async function showLicense(which) {
  const title = which === "license" ? t("Licenza di wmt-pdf (AGPL-3.0)") : t("Licenze dei componenti di terze parti");
  try {
    const { text } = await api(`/api/license/${which}`);
    infoDialog(title, "", { text });
  } catch {
    infoDialog(title, t("Il testo non è incluso in questa installazione: lo trovi sulla pagina del progetto."),
      { action: { label: t("Apri la licenza su GitHub"), run: () => openPage(`${PROJECT_PAGE}/blob/master/LICENSE`) } });
  }
}

async function checkNow() {
  toast(t("Ricerca di aggiornamenti…"));
  let info;
  try {
    info = await api("/api/update?now=1");
  } catch (error) {
    toast(error.message);
    return;
  }
  if (!info.enabled) {
    infoDialog(t("Aggiornamenti"), t("Il controllo degli aggiornamenti è disattivato su questo computer."));
  } else if (info.error) {
    infoDialog(t("Aggiornamenti"), t("Controllo non riuscito: {error}", { error: info.error }));
  } else if (info.available) {
    infoDialog(t("Aggiornamenti"), t("È disponibile la versione {latest} (installata: {current}).",
      { latest: info.latest, current: info.current }),
    { action: { label: t("Apri la pagina per scaricarla"), run: () => openPage(info.url) } });
  } else {
    infoDialog(t("Aggiornamenti"), t("Hai già l'ultima versione ({current}).", { current: info.current }));
  }
}

// --- comportamento del menu ------------------------------------------------------------------------------------------

export function initMenuBar(context) {
  ctx = context;
  const bar = $("menubar");
  for (const menu of menus()) {
    const title = el("button", { class: "menubar-title", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" });
    title.textContent = menu.title;
    title.addEventListener("click", () => (openTitle === title ? closeAll() : openMenu(title)));
    title.addEventListener("mouseenter", () => { if (openTitle && openTitle !== title) openMenu(title); });
    title.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        openMenu(title, true);
      }
    });
    title.menuItems = menu.items;
    bar.append(title);
  }
  $("info-close").addEventListener("click", () => $("info-dialog").close());
  document.addEventListener("pointerdown", (event) => {
    if (openTitle && !event.target.closest(".menubar, .menubar-menu")) closeAll();
  });
  window.addEventListener("blur", closeAll);
  window.addEventListener("resize", closeAll);
  document.addEventListener("keydown", (event) => {
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "p" && !event.shiftKey) {
      event.preventDefault(); // la stampa del browser stamperebbe l'interfaccia, non il documento
      if (hasDoc()) printDocument();
      return;
    }
    if (!openTitle) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeAll(true);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const inSub = event.target.closest(".menubar-menu.sub");
      if (event.key === "ArrowRight" && event.target.classList.contains("has-sub")) return; // apre il sottomenu
      if (event.key === "ArrowLeft" && inSub) {
        event.preventDefault();
        inSub.parentItem?.focus();
        inSub.remove();
        return;
      }
      event.preventDefault();
      const titles = [...document.querySelectorAll(".menubar-title")];
      const next = titles[(titles.indexOf(openTitle) + (event.key === "ArrowRight" ? 1 : -1) + titles.length) % titles.length];
      openMenu(next, true);
    }
  });
}

function closeAll(focusTitle = false) {
  document.querySelectorAll(".menubar-menu").forEach((m) => m.remove());
  document.querySelectorAll(".menubar-title").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (focusTitle && openTitle) openTitle.focus();
  openTitle = null;
}

async function openMenu(title, focusFirst = false) {
  closeAll();
  openTitle = title;
  title.setAttribute("aria-expanded", "true");
  const rect = title.getBoundingClientRect();
  const menu = await buildMenu(title.menuItems(), { left: rect.left, top: rect.bottom + 4 });
  if (openTitle !== title) return; // chiuso o cambiato mentre si preparava (Recenti chiede al server)
  document.body.append(menu);
  fitInWindow(menu);
  if (focusFirst) menu.querySelector("button:not(:disabled)")?.focus();
}

async function buildMenu(items, at, sub = false) {
  const menu = el("div", { class: `menubar-menu${sub ? " sub" : ""}`, role: "menu" });
  Object.assign(menu.style, { left: `${at.left}px`, top: `${at.top}px` });
  for (const item of await items) {
    if (item === "-") {
      menu.append(el("div", { class: "menubar-sep", role: "separator" }));
      continue;
    }
    const enabled = item.enabled ? item.enabled() : true;
    const button = el("button", { type: "button", role: item.checked ? "menuitemcheckbox" : "menuitem" });
    button.disabled = !enabled;
    const check = el("span", { class: "menubar-check" });
    if (item.checked) {
      const on = item.checked();
      button.setAttribute("aria-checked", String(on));
      check.textContent = on ? "✓" : "";
    }
    const label = el("span", { class: "menubar-label" });
    label.textContent = item.label;
    const keys = el("span", { class: "menubar-keys" });
    keys.textContent = item.submenu ? "›" : item.keys || "";
    button.append(check, label, keys);
    if (item.hint) button.title = item.hint;
    if (item.submenu) {
      button.classList.add("has-sub");
      button.setAttribute("aria-haspopup", "menu");
      const openSub = async (focus) => {
        if (button.disabled) return;
        menu.querySelectorAll(":scope ~ .menubar-menu.sub").forEach((m) => m.remove());
        document.querySelectorAll(".menubar-menu.sub").forEach((m) => { if (m.parentMenu === menu) m.remove(); });
        const r = button.getBoundingClientRect();
        const child = await buildMenu(item.submenu(), { left: r.right + 2, top: r.top - 6 }, true);
        if (!button.isConnected) return;
        child.parentMenu = menu;
        child.parentItem = button;
        document.body.append(child);
        fitInWindow(child, r);
        if (focus) child.querySelector("button:not(:disabled)")?.focus();
      };
      button.addEventListener("mouseenter", () => openSub(false));
      button.addEventListener("click", () => openSub(true));
      button.addEventListener("keydown", (event) => {
        if (event.key === "ArrowRight") {
          event.preventDefault();
          openSub(true);
        }
      });
    } else {
      button.addEventListener("mouseenter", () => {
        // Passando su un'altra voce si chiude il sottomenu aperto da questo menu.
        document.querySelectorAll(".menubar-menu.sub").forEach((m) => { if (m.parentMenu === menu) m.remove(); });
      });
      button.addEventListener("click", () => {
        closeAll();
        Promise.resolve(item.run()).catch((error) => toast(error.message));
      });
    }
    menu.append(button);
  }
  menu.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const buttons = [...menu.querySelectorAll(":scope > button:not(:disabled)")];
    const i = buttons.indexOf(document.activeElement);
    const next = buttons[(i + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length];
    next?.focus();
  });
  return menu;
}

// Un menu che uscirebbe dalla finestra si sposta dentro (i sottomenu, se serve, vanno a sinistra della voce).
function fitInWindow(menu, from = null) {
  const r = menu.getBoundingClientRect();
  if (r.right > window.innerWidth - 4) {
    menu.style.left = `${Math.max(4, from ? from.left - r.width - 2 : window.innerWidth - r.width - 4)}px`;
  }
  if (r.bottom > window.innerHeight - 4) menu.style.top = `${Math.max(4, window.innerHeight - r.height - 4)}px`;
}
