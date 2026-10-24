// Stampa del documento aperto: le pagine si disegnano con PDF.js in immagini e la finestra di stampa del sistema
// stampa solo quelle (vedi @media print in menubar.css). Funziona uguale nella finestra di wmt-pdf e nel browser.
import { $, el, t, toast } from "./common.js";
import { currentPdf } from "./doc.js";
import { applyFields } from "./fields.js";

const DPI = 150;
let busy = false;

export async function printDocument() {
  if (!currentPdf() || busy) return;
  busy = true;
  await applyFields(); // i campi scritti e non ancora applicati finiscono sul foglio, come si vedono
  const pdf = currentPdf();
  const root = $("print-root");
  root.innerHTML = "";
  const urls = [];
  toast(t("Preparazione della stampa…"));
  try {
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: DPI / 72 });
      const canvas = el("canvas");
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport, intent: "print" }).promise;
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      const url = URL.createObjectURL(blob);
      urls.push(url);
      const img = el("img", { class: "print-page", alt: "" });
      // Pagine orizzontali: il foglio segue la pagina.
      if (viewport.width > viewport.height) img.classList.add("landscape");
      img.src = url;
      root.append(img);
    }
    await Promise.all([...root.querySelectorAll("img")].map((img) => img.decode().catch(() => {})));
    window.print(); // si blocca finché la finestra di stampa è aperta
  } catch (error) {
    toast(t("Stampa non riuscita: {error}", { error: error.message }));
  } finally {
    root.innerHTML = "";
    urls.forEach((url) => URL.revokeObjectURL(url));
    busy = false;
  }
}
