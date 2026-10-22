# wmt-pdf

Lettura, modifica, conversione, OCR e verifica delle firme di PDF e `.p7m`, dalla riga di comando `wpdf`.
Fase 1: lettura per gli agenti (Claude Code compreso) come testo compatto invece che come immagini.
Fase 2: operazioni stile Stirling PDF (unione anche da Word, Excel, PowerPoint, email e immagini,
divisione, compressione, filigrane, password, evidenziazioni, esportazione, PDF ricercabile).
Il piano completo, con interfaccia grafica e firma PAdES/CAdES, è in [docs/PIANO.md](docs/PIANO.md).

## Installazione

Il modo più semplice: l'installatore per Windows o macOS dalle
[release](https://github.com/WebMarkeThink/wmt-pdf/releases) (tutto incluso, con il comando `wpdf`); su Linux un
comando di uv. Istruzioni e avvisi della prima apertura in [docs/INSTALLATORI.md](docs/INSTALLATORI.md).

Dai sorgenti serve [uv](https://docs.astral.sh/uv/). Funziona su Windows, macOS e Linux.

```bash
uv sync                     # base
uv sync --extra ocr         # con OCR (RapidOCR, modelli scaricati al primo uso)
uv sync --extra convert     # conversione da Word, Excel, PowerPoint, ODT, Markdown, HTML, email
uv sync --extra firma       # firma digitale, smart card, verifica sull'elenco europeo dei certificatori
uv sync --extra gui         # finestra nativa per l'interfaccia grafica (senza: si apre nel browser)
uv sync --all-extras        # tutto
uv run wpdf --help
```

Per averlo come comando globale: `uv tool install --from . "wmt-pdf[ocr,convert,firma,gui]"`.

## Interfaccia grafica

```bash
uv run wpdf gui
```

Due modi di lavorare, con la barra in alto sempre disponibile (Apri, Recenti, Unisci, Annulla, Ripeti, Salva,
Salva con nome, Scarica). Il documento aperto si chiude con la × accanto al nome o con Ctrl+W: se ci sono
modifiche non salvate si sceglie tra Salva, Non salvare e Annulla.

- **Documento**: si apre un PDF (Apri, trascinandolo nella finestra, o dai recenti) e lo si modifica direttamente.
  Con «Seleziona» (il puntatore) si vedono contornati gli oggetti della pagina (testo, immagini, firme grafiche,
  timbri, filigrane, evidenziazioni, note, campi dei moduli): clic per selezionarne uno, trascinamento per
  spostarlo, frecce per spostarlo di un punto (Maiusc: dieci), doppio clic su un testo per cambiarlo, Esc per
  deselezionare. Con «Modifica testo» il clic su un testo lo cambia o lo elimina; con «Aggiungi testo» si scrive
  nel punto scelto. «Aggiungi file…» unisce al documento altri file (PDF, Word, Excel, immagini, email), all'inizio
  o in fondo. Poi: ruota o elimina la pagina, cerca e sostituisci, evidenzia, e tutti gli altri strumenti
  applicati al documento aperto. Ogni modifica è una versione: si annulla e si ripete. Miniature delle pagine a
  sinistra.
- **Strumenti**: la griglia di icone per categoria (leggi, organizza, converti, ottimizza, modifica, moduli,
  proteggi e firma) per lavorare su uno o più file; i PDF risultanti si aprono come documento con «Apri».

Scorciatoie: Ctrl+O apri, Ctrl+S salva, Ctrl+Maiusc+S salva con nome, Ctrl+W chiudi il documento, Ctrl+Z annulla,
Ctrl+Y ripeti, frecce o PagSu/PagGiù per le pagine. Prima di chiudere con modifiche non salvate viene chiesta
conferma.

**Barra compatta** (solo nella finestra nativa): «Compatta» nella barra in alto e la scelta del lato (in alto, in
basso, a sinistra, a destra). La finestra si nasconde e resta una barra sempre in primo piano, agganciata a quel
lato dello schermo, con Apri, Recenti, Unisci, Converti, Comprimi, OCR e Firma. I file trascinati sulla barra si
aprono (un PDF) o si uniscono (più file); trascinati su un pulsante vanno a quello strumento. La freccia sposta la
barra sul lato successivo, la maniglia a sinistra la trascina dove si vuole; «Espandi» (o Esc) riporta la finestra
completa. Oltre a questa resta la normale riduzione a icona della finestra.

**Lingua**: italiano o inglese, dal selettore in basso a sinistra; la scelta resta tra una sessione e l'altra e vale
anche per i messaggi dei comandi eseguiti dall'interfaccia. Il documento aperto resta aperto al cambio di lingua.

Nella finestra nativa «Apri» e «Salva con nome» usano le finestre di dialogo del sistema, «Salva» riscrive il file
aperto e i file recenti restano tra una sessione e l'altra. Nel browser (senza l'extra `gui`) si lavora su copie:
«Salva» scarica il file e i recenti non ci sono.

Funziona su questo computer: un piccolo server su 127.0.0.1, raggiungibile solo dalla finestra (token di sessione,
controllo di host e origine); i file caricati e prodotti stanno in una cartella temporanea che si cancella alla
chiusura. Con l'extra `gui` si apre in una finestra nativa (pywebview, che su Windows usa WebView2); senza, nel browser.

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
| `wpdf export FILE --to docx\|xlsx\|html\|md\|txt\|json\|xml\|png\|jpg\|tiff` | esporta; xlsx prende le tabelle con i numeri italiani convertiti; json e xml danno la struttura completa (metadati, testo e righe con coordinate, tabelle, campi, annotazioni, segnalibri) |
| `wpdf ocr FILE -o out.pdf` | PDF ricercabile: testo invisibile sotto le pagine scansionate |

### Testo

| Comando | Cosa fa |
|---|---|
| `wpdf textobjects FILE --page N [--json]` | pezzi di testo della pagina con posizione, font e corpo |
| `wpdf replace FILE "vecchio" "nuovo" [--pages ...] -o out.pdf` | sostituisce un testo in tutto il documento |
| `wpdf edittext FILE --page N --at x,y --text "nuovo" -o out.pdf` | cambia il pezzo di testo in quel punto (testo vuoto: lo elimina); oppure `--index` |
| `wpdf objects FILE --page N [--json]` | oggetti della pagina che si possono spostare: testo, immagini, elementi grafici, annotazioni |
| `wpdf move FILE --page N --kind image --index I --by dx,dy -o out.pdf` | sposta un oggetto (`--kind` text, image, graphic, annotation); oppure `--to x,y` per l'angolo in basso a sinistra |
| `wpdf movetext FILE --page N --index I --by dx,dy -o out.pdf` | sposta un pezzo di testo (stesso font e caratteri); oppure `--to x,y` per la nuova linea di base |
| `wpdf addtext FILE --page N --at x,y --text "riga 1\nriga 2" [--size 11 --color #c00000 --bold] -o out.pdf` | scrive testo nuovo |

Le coordinate sono in punti PDF dall'angolo in basso a sinistra (A4: 595 × 842). Quando le lettere nuove sono nel
font del testo originale, la modifica avviene sul posto con lo stesso font; altrimenti (tipico dei font incorporati
in sottoinsieme, che contengono solo le lettere usate) il pezzo di testo viene riscritto nella stessa posizione e
dimensione con un font di sistema, e il comando lo dice. Un testo diviso dal programma che ha creato il PDF in più
pezzi non si sostituisce in un colpo solo: `replace` lo segnala, e i pezzi si cambiano con `edittext`.

### Moduli (fase 3)

| Comando | Cosa fa |
|---|---|
| `wpdf fields FILE [--json]` | campi con tipo, valore, opzioni, pagina, obbligatorietà |
| `wpdf fill FILE --data dati.json -o out.pdf [--flatten]` | compila; da un CSV con più righe crea un PDF per riga (`-o cartella`, `--name-column nome`) |
| `wpdf flatten FILE -o out.pdf` | rende i campi parte della pagina; evidenziazioni e note restano; rifiuta i PDF firmati |
| `wpdf form FILE --spec campi.json -o out.pdf` | aggiunge campi: testo, data, casella, elenco, lista, scelta singola, firma |

Specifica dei campi: ogni campo ha `name`, `type` e la posizione, data come `rect` `[x0, y0, x1, y1]` in punti
oppure come `after`: un'etichetta del testo, e il campo va subito alla sua destra. Opzioni: `width`, `height`, `page`,
`required`, `multiline`, `max_length`, `options`, `value`, `format` (per le date, es. `dd/mm/yyyy`).

```json
{"fields": [
  {"name": "nome", "type": "text", "after": "Il sottoscritto", "width": 220, "required": true},
  {"name": "nascita", "type": "date", "after": "Data di nascita"},
  {"name": "privacy", "type": "checkbox", "after": "Acconsento"},
  {"name": "firma", "type": "signature", "after": "Firma", "width": 180, "height": 40}
]}
```

`merge`, `pages` e `split` conservano campi e segnalibri; in `merge` i campi con lo stesso nome vengono rinominati
(`nome`, `nome+1`) e i segnalibri di ogni file finiscono sotto il segnalibro del file. Con `pages --order` i segnalibri
seguono il nuovo ordine delle pagine.

### Firme (fase 4)

| Comando | Cosa fa |
|---|---|
| `wpdf sign FILE --p12 cert.p12 --p12-password-env VAR -o out` | firma PAdES per i PDF, CAdES (`.p7m`, documento incluso) per gli altri file; `--format` per scegliere |
| `wpdf sign FILE --pkcs11 libreria.dll --pin-env VAR [--token-label ...] -o out` | firma con smart card o token, tramite la libreria PKCS#11 del produttore |
| `wpdf sign ... --after "Firma" --image autografa.png` | firma visibile accanto a un'etichetta (o `--rect`, o `--field` per un campo firma esistente) |
| `wpdf sign ... --tsa URL` | con marca temporale da un servizio RFC 3161 |
| `wpdf verify FILE [--offline] [--trust-root ca.pem]` | verifica completa: integrità, certificatore nell'elenco europeo, revoca, marca temporale, firma qualificata (QES) |
| `wpdf trust update [--country IT]` | scarica e verifica l'elenco europeo dei certificatori fidati; poi `verify` funziona anche senza rete |
| `wpdf stamp FILE --image autografa.png --after "In fede"` | firma solo grafica, senza valore di firma digitale |

Esiti di `verify`: 0 VALIDA, 2 NON VALIDA (documento alterato, firma rotta, certificato revocato),
3 NON VERIFICABILE (certificatore non riconosciuto, informazioni mancanti). Le firme successive si aggiungono
in modo incrementale: quelle già presenti restano valide. `p7m` e `signatures` restano i controlli rapidi
(solo integrità), `verify` è quello completo.

`info`, `text`, `search`, `tables` e `render` accettano anche un `.pdf.p7m`: il PDF viene estratto in automatico.
I comandi di lettura hanno `--json`. Codici d'uscita: 0 ok, 1 errore, 2 firma non valida.

Cosa fa `text` per risparmiare token:
- toglie intestazioni e piè di pagina ripetuti, citandoli una volta sola in testa (`--keep-headers` per tenerli);
- scrive le tabelle in Markdown e scarta i riquadri grafici scambiati per tabelle;
- mette segnaposto per le immagini invece delle immagini stesse;
- con `--max-tokens` si ferma tra una pagina e l'altra e dice da dove continuare.

## Lingua dei messaggi

I messaggi della CLI sono in italiano o in inglese: `wpdf --language en ...`, oppure la variabile `WPDF_LANG=en`;
senza nessuna delle due si usa la lingua del sistema (italiano se il sistema è in italiano, altrimenti inglese).
Le traduzioni stanno in `src/wmt_pdf/locales/en.json`, con il testo italiano come chiave; un test controlla che non
ne manchi nessuna (`uv run python tests/i18n_keys.py` elenca quelle da aggiungere).

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

- `verify --offline` non può controllare la revoca: l'esito è quindi NON VERIFICABILE. Online la revoca si verifica via OCSP/CRL,
  e si contattano gli indirizzi indicati nei certificati del file (lo fanno tutti i verificatori).
- La verifica è alla data di oggi: la validazione storica (firma valida alla data della marca temporale anche se il
  certificato è poi scaduto) non è ancora gestita.
- Firma con smart card: provata solo con certificati software (.p12); con un dispositivo vero va provata
  sul lettore e sulla libreria del produttore (InfoCert, Aruba, Namirial, carta CNS).
- Firma remota (Aruba, InfoCert, Namirial via API): non ancora, serve un contratto con il certificatore.
- Firme parallele su un `.p7m` (più firmatari nella stessa busta): si verificano tutte; per firmare si aggiunge
  una busta esterna (`.p7m.p7m`), come fanno anche i programmi di firma italiani.
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
- Interfaccia grafica: manca la password dei PDF protetti per gli strumenti diversi da «Sblocca», lo spostamento
  del testo col mouse e la modifica delle annotazioni esistenti; gli installatori sono da decidere (vedi il piano).
- `merge`, `pages` e `split`: i PDF protetti restano protetti, con la stessa password di apertura; per toglierla si usa `unlock`.
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
