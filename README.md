# wmt-pdf

Lettura, estrazione, OCR e verifica delle firme di PDF e `.p7m`.
Questa è la fase 1, la CLI `wpdf`: serve agli agenti, Claude Code compreso, per leggere i PDF
come testo compatto invece che come immagini delle pagine. Il piano completo, con interfaccia
grafica, operazioni stile Stirling PDF e firma PAdES/CAdES, è in [docs/PIANO.md](docs/PIANO.md).

## Installazione

Serve [uv](https://docs.astral.sh/uv/). Funziona su Windows, macOS e Linux.

```bash
uv sync                 # base
uv sync --extra ocr     # con OCR (RapidOCR, modelli scaricati al primo uso)
uv run wpdf --help
```

Per averlo come comando globale: `uv tool install --from . "wmt-pdf[ocr]"`.

## Comandi

| Comando | Cosa fa |
|---|---|
| `wpdf info FILE` | pagine, metadati, pagine scansionate o vuote, firme, token stimati |
| `wpdf text FILE [--pages 1-3] [--max-tokens N]` | testo per pagina in Markdown, con tabelle; OCR automatico sulle scansioni |
| `wpdf tables FILE [--csv]` | solo le tabelle |
| `wpdf search FILE "testo"` | righe trovate con numero di pagina, senza badare a maiuscole e accenti |
| `wpdf render FILE --page N [--crop x0,y0,x1,y1] [--gray]` | PNG di una pagina, o di una porzione, da guardare solo se serve |
| `wpdf p7m FILE.p7m [-o documento]` | firmatari, verifica di integrità, estrazione del documento; gestisce base64 e p7m annidati |
| `wpdf signatures FILE.pdf` | firme PAdES incorporate e loro integrità |

`info`, `text`, `search`, `tables` e `render` accettano anche un `.pdf.p7m`: il PDF viene estratto in automatico.
Tutti i comandi di lettura hanno `--json`. Codici d'uscita: 0 ok, 1 errore, 2 firma non valida.

Cosa fa `text` per risparmiare token:
- toglie intestazioni e piè di pagina ripetuti, citandoli una volta sola in testa (`--keep-headers` per tenerli);
- scrive le tabelle in Markdown e scarta i riquadri grafici scambiati per tabelle;
- mette segnaposto per le immagini invece delle immagini stesse;
- con `--max-tokens` si ferma tra una pagina e l'altra e dice da dove continuare.

## Uso con Claude Code

Da aggiungere al `CLAUDE.md` di un progetto, o a quello globale:

```markdown
## PDF e p7m
Non leggere i PDF con Read: usa `wpdf`.
1. `wpdf info FILE` per capire dimensioni, pagine scansionate e firme.
2. `wpdf text FILE --pages ... --max-tokens 8000` per il testo; continua con le pagine indicate se troncato.
3. `wpdf search FILE "termine"` per trovare dove si parla di qualcosa prima di leggere tutto.
4. `wpdf render FILE --page N --dpi 80 --gray` solo se serve vedere grafica, timbri o firme autografe; poi Read del PNG.
5. Per un .p7m: `wpdf p7m FILE` per la verifica, `wpdf text FILE` per leggerne il contenuto.
```

## Limiti attuali

- La verifica delle firme controlla l'integrità del contenuto e la corrispondenza col certificato.
  Non controlla ancora che il certificatore sia nell'elenco europeo dei fidati, né la revoca: è la fase 3.
- L'OCR su scansioni rumorose a 150 dpi ritrova circa l'80% delle parole: va bene per capire il documento, non per citarlo alla lettera.
- Ordine di lettura: segue quello di PDFium. Impaginazioni a più colonne complicate possono uscire mescolate.

## Sviluppo

```bash
uv sync --all-extras
uv run pytest                 # tutti i test, OCR compreso
uv run pytest -m "not ocr"    # senza OCR
```

Le fixture (PDF, p7m, firme PAdES con certificato di prova) sono generate dai test: nel repo non ci sono documenti binari.
Licenze: solo dipendenze permissive (vedi il piano), per lasciare aperta la scelta tra prodotto commerciale e open source.
