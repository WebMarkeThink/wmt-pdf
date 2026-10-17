# Fase 5: stampante PDF — progetto

Obiettivo: da qualsiasi programma, «Stampa» → stampante **wmt-pdf** → il PDF arriva a wmt-pdf, che può
salvarlo, unirlo ad altri, firmarlo, renderlo ricercabile o passarlo all'interfaccia grafica.

Nessun driver scritto da noi: si usano i meccanismi già presenti in ogni sistema, così non serve
firmare driver (Windows) né mantenere codice in kernel o in spooler.

## Windows

Driver **Microsoft Print To PDF** (incluso in Windows 10 e 11) su una porta che scrive in un file fisso,
senza la finestra «Salva con nome»:

```powershell
# da amministratore, una volta
Add-PrinterPort -Name "C:\ProgramData\wmt-pdf\coda\stampa.pdf"
Add-Printer -Name "wmt-pdf" -DriverName "Microsoft Print To PDF" -PortName "C:\ProgramData\wmt-pdf\coda\stampa.pdf"
```

`wpdf printer watch` osserva la cartella `coda`: quando il file è completo (dimensione stabile e chiuso
dallo spooler) lo sposta in `Documenti\wmt-pdf\Stampe\AAAA-MM-GG_hh-mm-ss.pdf` e applica l'azione scelta
(`--then open|ocr|merge-into FILE|sign ...`). Avvio automatico: attività pianificata all'accesso.

Da verificare su una macchina reale: comportamento con stampe simultanee (la porta è una sola: lo
spooler le serializza), permessi della cartella per utenti non amministratori, nome del documento
(lo spooler non lo passa al file: si legge dal registro dei lavori, `Get-PrintJob`, mentre la stampa è in corso).

## macOS

Nessun privilegio necessario: **PDF Services**. Un eseguibile in `~/Library/PDF Services/` compare nel
menu «PDF» della finestra di stampa di ogni app e riceve il percorso del PDF generato dal sistema.
`wpdf printer install` crea lì uno script «Invia a wmt-pdf» che chiama `wpdf printer receive <file>`.

## Linux

CUPS. Due strade:
- pacchetto `cups-pdf` della distribuzione (GPL, ma è un programma esterno installato dall'utente, non
  incluso in wmt-pdf) con cartella di uscita osservata da `wpdf printer watch`;
- backend CUPS proprio (uno script in `/usr/lib/cups/backend/`, serve root), più pulito ma da mantenere.

## Comandi previsti

| Comando | Cosa fa |
|---|---|
| `wpdf printer install` | crea la stampante (Windows: chiede l'elevazione; macOS: PDF Services; Linux: istruzioni) |
| `wpdf printer uninstall` | la toglie |
| `wpdf printer watch [--then ...]` | resta in ascolto e smista le stampe |
| `wpdf printer receive FILE` | riceve una stampa (usato da macOS e dal backend CUPS) |

## Decisioni per l'utente (IN ATTESA)

1. Va bene creare la stampante con il driver Microsoft già presente (nessun software di terzi)?
2. Dove salvare le stampe di default e quale azione automatica (solo salvare, aprire l'interfaccia, OCR)?
3. Linux: `cups-pdf` della distribuzione oppure backend proprio?
4. Prova di installazione su questo PC: serve l'autorizzazione da amministratore.
