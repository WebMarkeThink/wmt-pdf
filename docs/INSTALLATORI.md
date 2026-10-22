# Installatori

Per Windows e macOS c'è un installatore con tutto dentro (interfaccia, OCR, conversioni, firma e verifica) e il
comando `wpdf` per il terminale e per Claude Code. Non serve Python né uv.

## Installare

### Windows (10 e 11, 64 bit)

1. Scarica `wmt-pdf-<versione>-windows-x64-setup.exe` dalla release.
2. Avvialo. Per ora l'installatore non è firmato: Windows mostra «Windows ha protetto il PC» (editore
   sconosciuto). Fai clic su **Ulteriori informazioni → Esegui comunque**.
3. Si installa per il tuo utente, senza diritti di amministratore, in `%LOCALAPPDATA%\Programs\wmt-pdf`, con
   l'icona nel menu Start. L'opzione «Aggiungi il comando wpdf al PATH» (attiva di default) rende disponibile
   `wpdf` in ogni terminale aperto dopo l'installazione.

Serve WebView2, il motore di Edge, già presente in Windows 11 e nei Windows 10 aggiornati.
Si disinstalla da Impostazioni → App, che toglie anche `wpdf` dal PATH.

### macOS (11 o successivo)

1. Scarica il `.dmg` adatto al Mac: `macos-arm64` per Apple Silicon (M1 e successivi), `macos-x86_64` per Intel.
2. Aprilo e trascina **wmt-pdf** in **Applicazioni**.
3. Per ora l'app non è firmata da Apple: la prima volta fai **clic destro sull'app → Apri → Apri**. Se macOS dice
   che l'app «è danneggiata», è la quarantena dei file scaricati: `xattr -dr com.apple.quarantine /Applications/wmt-pdf.app`.
4. Per avere `wpdf` nel terminale (e per Claude Code):

   ```bash
   sudo ln -sf /Applications/wmt-pdf.app/Contents/MacOS/wpdf /usr/local/bin/wpdf
   ```

### Prima volta dell'OCR

I modelli dell'OCR (alcune decine di MB) si scaricano al primo uso e restano nella cache dell'utente; dopo
funziona anche senza rete. Lo stesso vale per l'elenco europeo dei certificatori (`wpdf trust update`).

## Costruire gli installatori

Con un tag di versione GitHub Actions costruisce tutto e allega i file alla release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Il flusso (`.github/workflows/installers.yml`) gira su Windows, macOS Apple Silicon e macOS Intel. Prima di
fare l'installatore prova il programma impacchettato (versione, info in inglese, testo e OCR su un PDF di prova): se
qualcosa manca nel pacchetto, la build si ferma lì. Si può lanciare anche a mano da Actions → Installatori → Run
workflow (i file restano tra gli artefatti dell'esecuzione).

Il repository è privato: i minuti di Actions si pagano oltre la quota gratuita e quelli macOS contano dieci volte;
una build completa ne usa circa 150-300.

In locale, su Windows (serve Inno Setup 6 per l'ultimo passo):

```bash
uv sync --all-extras --group build
uv run --group build python packaging/make_icons.py
uv run --group build pyinstaller --noconfirm --clean --workpath packaging/build/work --distpath packaging/dist packaging/wmt-pdf.spec
iscc /DAppVersion=0.1.0 packaging/windows/wmt-pdf.iss
```

Su un Mac, dopo PyInstaller: `sh packaging/macos/make_dmg.sh 0.1.0 arm64`.

## File

| File | Cosa fa |
| --- | --- |
| `packaging/wmt-pdf.spec` | PyInstaller: una sola analisi, due programmi (`wmt-pdf` finestra, `wpdf` console) che condividono le librerie; su macOS `wmt-pdf.app` |
| `packaging/launch_gui.py`, `launch_cli.py` | avvio dei due programmi |
| `packaging/make_icons.py` | icona (.ico, .icns, .png) generata con Pillow |
| `packaging/windows/wmt-pdf.iss` | installatore Inno Setup: per utente, menu Start, PATH, disinstallazione |
| `packaging/macos/make_dmg.sh` | immagine disco con il collegamento ad Applicazioni |

## Firma del codice (da fare)

Senza firma gli installatori funzionano, ma con gli avvisi descritti sopra. Per toglierli:

- **Windows**: un certificato di firma del codice (OV o EV) oppure Azure Trusted Signing; nel flusso si aggiunge un
  passo `signtool` sull'eseguibile e sull'installatore, con il certificato nei segreti del repository.
- **macOS**: Apple Developer Program (99 $ l'anno), certificato «Developer ID Application», firma con hardened
  runtime e notarizzazione (`xcrun notarytool`), poi `xcrun stapler` sul .dmg.
