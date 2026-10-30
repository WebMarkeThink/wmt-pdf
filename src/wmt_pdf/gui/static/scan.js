// PDF scansionati: all'apertura si controlla se le pagine sono solo immagini e si propone il riconoscimento del testo
// (OCR), oppure lo si fa subito se è stato scelto «Sempre all'apertura». Il risultato è una versione nuova del documento.
import { $, api, lang, post, t, toast } from "./common.js";

const scan = {
  hooks: null, // { apply, message, tools, onDone, current, fileId: () => id e file del documento mostrato }
  auto: false,
  closed: new Set(), // documenti per cui la proposta è stata chiusa
  tried: new Set(), // versioni uscite dall'OCR: su quelle non si ripropone (tornando indietro con Annulla sì)
  running: new Set(), // documenti con l'OCR in corso
  run: 0,
};

export async function initScan(hooks) {
  scan.hooks = hooks;
  $("scan-run").addEventListener("click", () => recognize());
  $("scan-close").addEventListener("click", () => {
    scan.closed.add(hooks.current());
    $("scan-bar").hidden = true;
  });
  $("scan-auto").addEventListener("change", async () => {
    const box = $("scan-auto");
    try {
      await post("/api/settings", { auto_ocr: box.checked });
      scan.auto = box.checked;
    } catch (error) {
      toast(error.message);
      box.checked = !box.checked;
      return;
    }
    if (box.checked) recognize();
  });
  try {
    scan.auto = (await api("/api/settings")).auto_ocr === true;
    $("scan-auto").checked = scan.auto;
  } catch {
    // senza impostazioni resta la proposta
  }
}

// Documento caricato (aperto, scheda cambiata, versione nuova).
export async function checkScan(state) {
  const run = ++scan.run;
  $("scan-bar").hidden = true;
  if (scan.closed.has(state.id) || scan.tried.has(state.file_id) || scan.running.has(state.id)) return;
  let status;
  try {
    status = await api(`/api/doc/${state.id}/scan`);
  } catch {
    return; // non si riesce a controllare: nessuna proposta, il documento si legge lo stesso
  }
  if (run !== scan.run || !status.pages.length || status.signed || !status.available) return;
  if (scan.auto) {
    recognize();
    return;
  }
  const { pages, total } = status;
  $("scan-text").textContent = pages.length === total
    ? t("Questo PDF è una scansione: il testo non si può selezionare né cercare.")
    : t("{n} pagine su {total} sono scansioni: il loro testo non si può selezionare né cercare.", { n: pages.length, total });
  $("scan-bar").hidden = false;
}

// «Modifica testo» su una scansione senza testo: si riconosce subito, come fa Acrobat. Dice cosa non si può fare.
export async function recognizeForEdit() {
  const id = scan.hooks.current();
  if (!id || scan.running.has(id)) return; // già in corso: la barra lo dice
  if (scan.tried.has(scan.hooks.fileId())) {
    scan.hooks.message(t("Su questa pagina non è stato riconosciuto nessun testo"));
    return;
  }
  const status = await api(`/api/doc/${id}/scan`).catch(() => null);
  if (!status || scan.hooks.current() !== id) return;
  if (status.signed) scan.hooks.message(t("PDF firmato: modificarlo renderebbe la firma non valida"), true);
  else if (!status.available) scan.hooks.message(t("Il riconoscimento del testo non è installato"), true);
  else await recognize(true, id);
}

async function recognize(forEdit = false, id = scan.hooks.current()) {
  if (!id || scan.running.has(id) || scan.hooks.current() !== id) return;
  scan.running.add(id);
  $("scan-run").disabled = true;
  $("scan-bar").hidden = false;
  $("scan-text").textContent = t("Riconoscimento del testo in corso: può richiedere qualche minuto…");
  const tool = scan.hooks.tools().find((item) => item.id === "ocr");
  const choices = tool?.options.find((o) => o.name === "lang")?.choices || [];
  const applied = await scan.hooks.apply("ocr", { lang: choices.includes(lang()) ? lang() : "it" });
  scan.running.delete(id);
  $("scan-run").disabled = false;
  $("scan-bar").hidden = true;
  if (!applied) return; // non è partito o non è riuscito: il messaggio è nel pannello, si potrà riprovare
  // Una volta sola per versione: una pagina senza testo riconoscibile non deve riproporlo all'infinito.
  scan.tried.add(scan.hooks.fileId());
  const status = await api(`/api/doc/${id}/scan`).catch(() => null);
  if (status?.pages.length === status?.total) scan.hooks.message(t("Su questa pagina non è stato riconosciuto nessun testo"));
  else scan.hooks.onDone(forEdit);
}
