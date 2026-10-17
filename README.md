# wmt-pdf

Lettura, modifica, conversione, OCR e verifica delle firme di PDF e `.p7m`, dalla riga di comando `wpdf`.
Fase 1: lettura per gli agenti (Claude Code compreso) come testo compatto invece che come immagini.
Fase 2: operazioni stile Stirling PDF (unione anche da Word, Excel, PowerPoint, email e immagini,
divisione, compressione, filigrane, password, evidenziazioni, esportazione, PDF ricercabile).
Il piano completo, con interfaccia grafica e firma PAdES/CAdES, è in [docs/PIANO.md](docs/PIANO.md).

## Installazione

Serve [uv](https://docs.astral.sh/uv/). Funziona su Windows, macOS e Linux.

```bash
uv sync                     # base
uv sync --extra ocr         # con OCR (RapidOCR, modelli scaricati al primo uso)
uv sync --extra convert     # conversione da Word, Excel, PowerPoint, ODT, Markdown, HTML, email
uv sync --all-extras        # tutto
uv run wpdf --help
```

Per averlo come comando globale: `uv tool install --from . "wmt-pdf[ocr,convert]"`.

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

### Operazioni (fase 2)

Tutte scrivono in `-o`; non sovrascrivono mai il file di partenza e un file esistente solo con `--force`.
Per i PDF protetti: `--password` oppure `--password-env NOME_VARIABILE` (non resta nella cronologia della shell).

| Comando | Cosa fa |
|---|---|
| `wpdf merge A B C... -o out.pdf` | unisce file anche di formati diversi, un segnalibro per file; `--attachments` accoda gli allegati delle email |
| `wpdf convert FILE -o out.pdf` | da Word, Excel, PowerPoint, ODT, Markdown, testo, HTML, CSV, email `.eml`, immagini (TIFF multipagina compreso) |
| `wpdf split FILE --every N` / `--ranges "1-3;4-"` `-o cartella` | divide in più file |
| `wpdf pages FILE --keep 1-3` / `--delete 2` / `--order 3,1,2` | tiene, toglie o riordina pagine |
| `wpdf rotate FILE --angle 90 [--pages 2]` | ruota |
| `wpdf meta FILE --title ... --author ... [--clear]` | metadati |
| `wpdf protect FILE --new-password X [--no-print --no-copy --no-edit]` | password AES-256 e permessi |
| `wpdf unlock FILE --password X` | toglie la password |
| `wpdf optimize FILE --profile email\|standard\|stampa\|lossless` | riduce le dimensioni; non ingrandisce mai il file |
| `wpdf watermark FILE --text "BOZZA"` / `--image logo.png` | filigrana, marcata come tale |
| `wpdf unwatermark FILE` | toglie le filigrane marcate (di wpdf, Acrobat e altri editor) |
| `wpdf highlight FILE "testo" [--note ... --color verde]` | evidenzia ogni occorrenza |
| `wpdf annotations FILE [--remove -o out.pdf]` | elenca (con il testo evidenziato) o toglie le annotazioni |
| `wpdf export FILE --to docx\|xlsx\|html\|md\|txt\|png\|jpg\|tiff` | esporta; xlsx prende le tabelle con i numeri italiani convertiti |
| `wpdf ocr FILE -o out.pdf` | PDF ricercabile: testo invisibile sotto le pagine scansionate |

`info`, `text`, `search`, `tables` e `render` accettano anche un `.pdf.p7m`: il PDF viene estratto in automatico.
I comandi di lettura hanno `--json`. Codici d'uscita: 0 ok, 1 errore, 2 firma non valida.

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
- Conversioni da Office: contenuto e struttura sì (titoli, paragrafi, tabelle, immagini), impaginazione identica no.
  Excel: colonne di uguale larghezza; PowerPoint: testi e immagini al loro posto ma senza sfondi, forme e temi;
  immagini EMF/WMF saltate. Email: solo `.eml` (il formato `.msg` di Outlook richiederebbe una libreria GPL).
- Le conversioni da HTML ed email non scaricano nulla da internet e leggono solo le immagini nella cartella del documento;
  un'email non legge mai file del computer.
- Filigrane: si tolgono solo quelle marcate come tali; una filigrana disegnata come testo normale non è distinguibile dal contenuto.
- Ottimizzazione: ricomprime le immagini a colori e in scala di grigi; non tocca CMYK, maschere e immagini in bianco e nero, né i font.
- PDF/A non ancora supportato.
- `merge`, `pages` e `split` creano un documento nuovo: i campi dei moduli e i segnalibri dei file di partenza non vengono
  riportati (fase 3). I PDF protetti restano protetti, con la stessa password di apertura; per toglierla si usa `unlock`.
- Conversione di un `.html` o `.md`: può includere immagini della sua cartella (e sottocartelle). Per un HTML scaricato
  da fonti non fidate conviene spostarlo prima in una cartella a sé.

## Sviluppo

```bash
uv sync --all-extras
uv run pytest                 # tutti i test, OCR compreso
uv run pytest -m "not ocr"    # senza OCR
```

Le fixture (PDF, p7m, firme PAdES con certificato di prova) sono generate dai test: nel repo non ci sono documenti binari.
Licenze: solo dipendenze permissive (vedi il piano), per lasciare aperta la scelta tra prodotto commerciale e open source.
