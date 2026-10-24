// Stampa del documento aperto: le pagine si disegnano con PDF.js in immagini e la finestra di stampa del sistema
// stampa solo quelle (vedi @media print in menubar.css). Funziona uguale nella finestra di wmt-pdf e nel browser.
import { $, el, t, toast } from "./common.js";
import { currentPdf } from "./doc.js";
import { applyFields } from "./fields.js";

const LONG = 60; // pagine: oltre, risoluzione più bassa (la memoria della finestra non è infinita)
let busy = false;

export async function printDocument() {
  if (!currentPdf() || busy) return;
  busy = true;
  const root = $("print-root");
  const urls = [];
  try {
    // I campi scritti e non ancora applicati finiscono sul foglio, come si vedono.
    if (!(await applyFields())
        && !window.confirm(t("I campi scritti non si possono applicare (vedi il messaggio). Stampare il documento senza?"))) {
      return;
    }
    const pdf = currentPdf();
    const dpi = pdf.numPages > LONG ? 110 : 150;
    root.innerHTML = "";
    for (let n = 1; n <= pdf.numPages; n++) {
      if (n === 1 || n % 10 === 0) toast(t("Preparazione della stampa: pagina {n} di {count}…", { n, count: pdf.numPages }));
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: dpi / 72 });
      const canvas = el("canvas");
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport, intent: "print" }).promise;
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      canvas.width = 0; // la tela non serve più: si libera subito, come le risorse della pagina
      page.cleanup();
      const url = URL.createObjectURL(blob);
      urls.push(url);
      const img = el("img", { class: "print-page", alt: "" });
      if (viewport.width > viewport.height) img.classList.add("landscape"); // foglio orizzontale (menubar.css)
      await new Promise((resolve) => {
        img.onload = img.onerror = resolve;
        img.src = url;
      });
      root.append(img);
    }
    // Nella finestra di wmt-pdf come in Chrome la stampa blocca finché la finestra è aperta; afterprint vale
    // anche dove non blocca, così le pagine non spariscono prima di essere stampate.
    const done = new Promise((resolve) => window.addEventListener("afterprint", resolve, { once: true }));
    window.print();
    await Promise.race([done, new Promise((resolve) => setTimeout(resolve, 10 * 60 * 1000))]);
  } catch (error) {
    toast(t("Stampa non riuscita: {error}", { error: error.message }));
  } finally {
    root.innerHTML = "";
    urls.forEach((url) => URL.revokeObjectURL(url));
    busy = false;
  }
}
