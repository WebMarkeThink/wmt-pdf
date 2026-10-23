// Campi del modulo nella vista documento: si compilano scrivendo direttamente sulla pagina, come in Acrobat.
// I valori scritti restano in memoria (anche cambiando pagina) finché non si applicano: allora diventano una
// versione nuova del documento con lo strumento «fill», che rigenera l'aspetto dei campi.
import { $, el, post, t, upload } from "./common.js";

const WIDGET = 20; // pdfjs.AnnotationType.WIDGET
const form = {
  docId: null,
  count: 0, // campi compilabili nel documento
  countRun: 0,
  byDoc: new Map(), // id del documento → (nome del campo → valore scritto e non ancora applicato)
  hooks: null, // { apply, message, updateBar, redraw, currentId } da doc.js
  reported: new Map(), // id del documento → numero di campi in sospeso già comunicato al server
};

export function initFields(hooks) {
  form.hooks = hooks;
  $("form-apply").addEventListener("click", () => applyFields());
  $("form-reset").addEventListener("click", () => {
    pendingOf().clear();
    changed();
    form.hooks.redraw();
  });
  $("form-highlight").addEventListener("change", () => {
    $("field-layer").classList.toggle("plain", !$("form-highlight").checked);
  });
}

// I valori scritti e non ancora applicati del documento mostrato: restano con il documento, anche cambiando
// scheda o dopo un'altra modifica (una rotazione, una pagina tolta).
function pendingOf(id = form.docId) {
  if (!form.byDoc.has(id)) form.byDoc.set(id, new Map());
  return form.byDoc.get(id);
}

// Documento caricato (aperto, scheda cambiata o versione nuova): quanti campi ha.
export async function loadFields(state, pdf) {
  form.docId = state.id;
  form.count = 0;
  refreshPanel();
  // getFieldObjects() di PDF.js qui torna vuoto: i campi si contano dai widget delle pagine, senza bloccare.
  const run = ++form.countRun;
  const names = new Set();
  for (let n = 1; n <= pdf.numPages; n++) {
    let widgets;
    try {
      widgets = await pageWidgets(await pdf.getPage(n));
    } catch {
      return; // documento chiuso o sostituito nel frattempo
    }
    if (run !== form.countRun) return;
    widgets.filter((a) => !a.readOnly).forEach((a) => names.add(a.fieldName));
  }
  form.count = names.size;
  refreshPanel();
}

// Documento chiuso: i suoi valori in sospeso spariscono con lui.
export function forgetFields(id) {
  form.byDoc.delete(id);
  form.reported.delete(id);
  form.countRun++;
  if (form.docId !== id) return;
  form.docId = null;
  form.count = 0;
  $("field-layer").innerHTML = "";
  refreshPanel();
}

export const pendingFields = (id = form.docId) => form.byDoc.get(id)?.size ?? 0;

// Il server deve saperlo: alla chiusura della finestra chiede conferma anche per i campi non ancora applicati.
function changed() {
  refreshPanel();
  form.hooks.updateBar();
  const id = form.docId;
  const count = pendingFields(id);
  if (!id || form.reported.get(id) === count) return;
  form.reported.set(id, count);
  post(`/api/doc/${id}/pending`, { count }).catch(() => form.reported.delete(id));
}

// I widget compilabili della pagina. La pagina li disegna comunque (PDF.js, con i moduli attivi, toglierebbe anche
// pulsanti ed elenchi a scelta multipla): il campo vero sta sopra, su un fondo pieno che copre l'aspetto vecchio.
export async function pageWidgets(page) {
  const annotations = await page.getAnnotations({ intent: "display" });
  return annotations.filter((a) => a.annotationType === WIDGET && !a.hidden && kindOf(a));
}

function kindOf(a) {
  if (a.fieldType === "Tx") return a.multiLine ? "textarea" : "text";
  if (a.fieldType === "Btn" && a.checkBox) return "checkbox";
  if (a.fieldType === "Btn" && a.radioButton) return "radio";
  if (a.fieldType === "Ch" && !a.multiSelect) return "choice";
  return null; // pulsanti, firme, elenchi a scelta multipla: restano come li disegna la pagina
}

export function drawFields(widgets, viewport) {
  const layer = $("field-layer");
  layer.innerHTML = "";
  // Ordine di lettura: dall'alto in basso, poi da sinistra; Tab passa al campo successivo.
  const placed = widgets.map((a) => {
    const [x0, y0] = viewport.convertToViewportPoint(a.rect[0], a.rect[1]);
    const [x1, y1] = viewport.convertToViewportPoint(a.rect[2], a.rect[3]);
    return { a, left: Math.min(x0, x1), top: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
  }).sort((p, q) => (Math.abs(p.top - q.top) > 4 ? p.top - q.top : p.left - q.left));
  for (const box of placed) layer.append(fieldInput(box, viewport.scale));
}

function fieldInput({ a, left, top, width, height }, scale) {
  const kind = kindOf(a);
  const name = a.fieldName;
  const box = el("span", { class: `field-box box-${kind}` });
  Object.assign(box.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
  let input;
  if (kind === "textarea") input = el("textarea");
  else if (kind === "choice") {
    input = el("select");
    // Una scelta vuota solo se il campo è vuoto: «fill» non svuota un elenco, lascerebbe il valore di prima.
    const current = Array.isArray(a.fieldValue) ? a.fieldValue[0] : a.fieldValue;
    if (!current && !a.options.some((o) => o.exportValue === "")) input.append(el("option", { value: "" }));
    for (const option of a.options) {
      const item = el("option", { value: option.exportValue });
      item.textContent = option.displayValue ?? option.exportValue;
      input.append(item);
    }
  } else input = el("input", { type: kind === "text" ? "text" : kind });
  input.classList.add("field", `field-${kind}`);
  input.dataset.name = name;
  input.title = a.alternativeText || name;
  input.setAttribute("aria-label", a.alternativeText || name);
  if (a.readOnly) input.disabled = true;
  const size = a.defaultAppearanceData?.fontSize;
  const fontSize = size ? size * scale : Math.min(height * 0.7, 14 * scale);
  input.style.fontSize = `${Math.max(fontSize, 6)}px`;
  if (a.textAlignment === 1) input.style.textAlign = "center";
  if (a.textAlignment === 2) input.style.textAlign = "right";

  const pending = pendingOf();
  const saved = pending.has(name) ? pending.get(name) : null;
  if (kind === "checkbox") {
    input.checked = saved !== null ? saved : a.fieldValue === a.exportValue;
    input.addEventListener("change", () => setValue(name, input.checked, input));
  } else if (kind === "radio") {
    input.name = `radio-${name}`;
    input.value = a.buttonValue;
    input.checked = (saved !== null ? saved : a.fieldValue) === a.buttonValue;
    input.addEventListener("change", () => input.checked && setValue(name, a.buttonValue, input));
  } else {
    const value = saved !== null ? saved : a.fieldValue;
    input.value = Array.isArray(value) ? value[0] ?? "" : value ?? "";
    if (a.maxLen && kind !== "choice") input.maxLength = a.maxLen;
    input.addEventListener("input", () => setValue(name, input.value, input));
  }
  box.append(input);
  return box;
}

// Lo stesso campo può comparire più volte (stesso nome su più punti o pagine): si aggiornano tutti.
function setValue(name, value, source) {
  pendingOf().set(name, value);
  for (const other of $("field-layer").querySelectorAll(".field")) {
    if (other === source || other.dataset.name !== name) continue;
    if (other.type === "checkbox") other.checked = Boolean(value);
    else if (other.type === "radio") other.checked = other.value === value;
    else other.value = value;
  }
  changed();
}

function refreshPanel() {
  $("doc-form").hidden = !form.count;
  const pending = pendingFields();
  $("form-info").textContent = pending
    ? (pending === 1 ? t("1 campo compilato, non ancora applicato") : t("{n} campi compilati, non ancora applicati", { n: pending }))
    : (form.count === 1 ? t("1 campo: scrivi direttamente sulla pagina") : t("{n} campi: scrivi direttamente sulla pagina", { n: form.count }));
  $("form-apply").disabled = !pending;
  $("form-reset").disabled = !pending;
}

// I valori diventano una versione nuova del documento. Vero se applicati (o se non c'era niente da applicare).
export async function applyFields() {
  if (form.docId !== form.hooks.currentId()) return true; // scheda appena cambiata: questi valori non sono suoi
  const pending = pendingOf();
  if (!pending.size) return true;
  const values = Object.fromEntries(pending);
  let info;
  try {
    const file = new File([JSON.stringify(values)], "campi.json", { type: "application/json" });
    info = await upload(file);
  } catch (error) {
    form.hooks.message(error.message, true);
    return false;
  }
  const applied = await form.hooks.apply("fill", { data: info.id });
  // Solo i valori applicati: quello che si è scritto nel frattempo resta da applicare.
  if (applied) for (const [key, value] of Object.entries(values)) if (pending.get(key) === value) pending.delete(key);
  changed();
  return applied;
}
