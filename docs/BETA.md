**Versione di prova di wmt-pdf**: leggere, modificare, convertire, firmare e verificare PDF e `.p7m`.
È una beta: grazie se la provi e ci dici cosa non va.

## Quale file scaricare

| Computer | File |
| --- | --- |
| Windows 10 o 11 | `wmt-pdf-…-windows-x64-setup.exe` |
| Mac con chip Apple (M1, M2, M3, M4…) | `wmt-pdf-…-macos-arm64.dmg` |
| Mac con processore Intel | `wmt-pdf-…-macos-x86_64.dmg` |

Sul Mac serve macOS 15 (Sequoia) o successivo. Per sapere che chip hai: menu Apple → Informazioni su questo Mac.

## La prima apertura

Il programma non è ancora firmato digitalmente, quindi il sistema avvisa:

- **Windows**: «Windows ha protetto il PC» → **Ulteriori informazioni** → **Esegui comunque**. Si installa senza
  diritti di amministratore.
- **Mac**: apri il `.dmg`, trascina **wmt-pdf** in **Applicazioni**, poi la prima volta **clic destro sull'app →
  Apri → Apri**. Se dice che l'app «è danneggiata», nel Terminale:
  `xattr -dr com.apple.quarantine /Applications/wmt-pdf.app`

I file restano sul computer: niente viene caricato su server esterni. La rete serve solo a scaricare, la prima
volta, i modelli dell'OCR e l'elenco europeo dei certificatori, e a chiedere a GitHub una volta al giorno se c'è una
versione nuova (si disattiva con «Controlla aggiornamenti» in basso a sinistra).

## Cosa provare

- Aprire PDF diversi (fatture, contratti, scansioni) e scorrere le pagine; più documenti insieme, ognuno nella sua
  scheda in alto (Ctrl+Tab per passare dall'uno all'altro).
- Su Windows: scegliere wmt-pdf come app predefinita per i PDF (opzione alla fine dell'installazione, oppure
  «App predefinita per i PDF…» in basso a sinistra) e aprire i PDF con un doppio clic, anche con l'app già aperta.
- **Seleziona**: spostare testi, immagini, firme e timbri trascinandoli; doppio clic su un testo per cambiarlo.
- **Aggiungi testo**, cerca e sostituisci, evidenzia; **Annulla** e **Ripeti**; **Salva** e **Salva con nome**.
- **Organizza pagine…** nel documento: trascinare le miniature per riordinare (Ctrl o Maiusc per più pagine), ruotare,
  eliminare con Canc, doppio clic per vedere la pagina in grande, inserire altri file nel punto voluto.
- **Unisci** più file (anche Word, Excel, immagini, email): ordinare e togliere le pagine prima di creare il PDF.
- Gli strumenti della griglia: comprimi, converti, esporta, OCR, filigrana, password, moduli.
- Verifica di PDF firmati e di file `.p7m`; firma grafica.
- **Compatta**: la barra sempre in primo piano su un lato dello schermo.
- La lingua (italiano o inglese) dal selettore in basso a sinistra.

## Aggiornamenti

Quando esce una versione nuova, nella barra in alto compare **Aggiornamento**: apre la pagina da cui scaricarla.
Si installa sopra quella attuale (su Windows l'installatore sostituisce la vecchia, sul Mac si trascina di nuovo in
Applicazioni); le impostazioni restano. Dal terminale: `wpdf update`.

## Segnalare un problema

Apri una segnalazione in **Issues** (serve un account GitHub), oppure rispondi a chi ti ha mandato il link.
Scrivi cosa stavi facendo, cosa ti aspettavi e cosa è successo, con il sistema (Windows/Mac) e, se puoi, uno
screenshot. Non allegare documenti con dati personali o riservati.

---

*Test version of wmt-pdf (Italian and English interface): download the file for your system above; the program is
not signed yet, so allow it on first launch as described. Please report problems in Issues.*
