# wmt-pdf — piano

Programma per leggere, modificare, estrarre, fare OCR e firmare PDF, con verifica dei p7m.
Un mix tra Acrobat Pro e Stirling PDF, con interfaccia ad icone. Si parte dalla CLI per gli agenti.

## Scelte

- **Licenza ancora aperta** (prodotto o open source): solo dipendenze permissive (MIT, BSD, Apache, MPL).
  Esclusi MuPDF/PyMuPDF/pymupdf4llm e iText (AGPL), marker (GPL).
- **Tutti i sistemi**: Python 3.12 gestito da uv; niente binari esterni obbligatori.
- **Firma**: sia con dispositivo (PKCS#11, smart card/token) sia remota (API dei certificatori).

| Funzione | Libreria | Licenza |
|---|---|---|
| Testo, render pagine | pypdfium2 (PDFium) | BSD-3 / Apache-2.0 |
| Tabelle, parole con coordinate | pdfplumber (pdfminer.six) | MIT |
| OCR (extra `ocr`) | RapidOCR + onnxruntime | Apache-2.0 / MIT |
| p7m (CAdES) | asn1crypto + cryptography | MIT / Apache-BSD |
| PAdES | pyHanko | MIT |
| Verifica su certificatori fidati (fase 3) | EU DSS (Java) o pyHanko + EU LOTL | LGPL / MIT |

## Fasi

1. **CLI per agenti** (questa): `info`, `text`, `search`, `tables`, `render`, `p7m`, `signatures`.
   Obiettivo: Claude legge un PDF come testo strutturato, le immagini solo su richiesta.
2. **Operazioni** (stile Stirling), tutte anche da CLI:
   - unisci, dividi, ruota, estrai e riordina pagine, metadati;
   - **unione da formati diversi**: immagini, Office (docx, xlsx, pptx, odt), HTML, testo, email → PDF, poi unione;
   - **ottimizzazione e riduzione**: ricompressione immagini con DPI e qualità scelte, rimozione di font duplicati e oggetti inutili, linearizzazione per il web (qpdf/pikepdf, Apache/MPL), profili «email», «archivio», «stampa»;
   - **esportazione**: Word, Excel (tabelle), immagini PNG/JPEG/TIFF, testo e Markdown, HTML, PDF/A per l'archiviazione;
   - **filigrane**: aggiunta di testo o immagine, rimozione di quelle riconoscibili;
   - **password e permessi**: protezione AES-256, rimozione con la password nota;
   - **evidenziazioni e annotazioni**: evidenzia, sottolinea, barra, note, timbri; elenco ed esportazione delle annotazioni;
   - PDF ricercabile da scansione (strato di testo OCR sotto l'immagine).
3. **Moduli**: creazione di PDF compilabili (campi testo, caselle, scelte, date, campi firma), compilazione da dati (JSON/CSV) e appiattimento.
4. **Firme**: verifica completa (catena, revoca, elenco europeo dei certificatori fidati); firma PAdES e CAdES con PKCS#11; firma remota; firma grafica (immagine + campo).
5. **Stampante PDF**: stampante virtuale di sistema che salva in PDF e può passare il file all'app (unione, firma, OCR).
   Per sistema: Windows con driver di stampa (Microsoft Print to PDF non basta per l'integrazione; serve un port monitor o un driver v4 proprio), macOS con una voce del menu PDF o una coda CUPS, Linux con una coda CUPS. È la parte più legata al sistema: va progettata a sé, con firma del driver su Windows.
6. **Interfaccia grafica**: Tauri (Windows, macOS, Linux), griglia di funzioni ad icone, visualizzatore PDF.js con modifica di testo e annotazioni; usa lo stesso motore della CLI.

Fase 2, scelta del 2026-10-17: librerie dedicate per ogni formato, niente LibreOffice. pikepdf (MPL) per le operazioni;
mammoth (BSD) + xhtml2pdf (Apache) per Word; openpyxl, python-pptx, markdown, odfpy (opzione Apache) per gli altri formati.
xhtml2pdf porta svglib, LGPL-3: compatibile anche con un prodotto commerciale se usata senza modificarla, da ricordare
quando si sceglie la licenza. Esclusi: pdf2docx (dipende da PyMuPDF, AGPL), extract-msg (.msg, GPL), WeasyPrint (GTK su Windows).
Rimandati: PDF/A, larghezze di colonna da Excel, rimozione di filigrane non marcate.

Da verificare prima della fase 3: le conversioni da Office richiedono LibreOffice in modalità senza interfaccia (MPL) da installare a parte, oppure librerie dedicate per ciascun formato. La modifica del testo già presente in un PDF (stile Acrobat) è la funzione più difficile senza MuPDF: valutare PDFium più pdf-lib (MIT) nell'interfaccia.

## Regole della CLI per agenti

- Output predefinito in Markdown compatto, `--json` per le macchine.
- Ogni pagina con intestazione `## Pagina N`; tabelle in Markdown; immagini solo come segnaposto `[immagine …]`.
- `--pages` per gli intervalli, `--max-tokens` come budget (stima: caratteri/4) con indicazione di come continuare.
- OCR `auto`: solo sulle pagine senza testo; `force` e `never` disponibili.
- Un `.p7m` passato a `text`/`info`/`search` viene aperto in automatico se contiene un PDF.
- Errori brevi su stderr, codici d'uscita: 0 ok, 1 errore d'uso o file, 2 firma non valida.
