"""PDF fedeli dai documenti Office: li impagina il programma che li sa leggere, se c'è sul computer.

- Microsoft Office su Windows (Word, Excel, PowerPoint): esportazione in PDF via COM, da PowerShell, senza
  finestre e con le macro disattivate; i documenti protetti da password si rifiutano invece di aprire una richiesta.
- LibreOffice (Windows, macOS, Linux): ``soffice --headless --convert-to pdf`` con un profilo temporaneo, così non
  disturba un LibreOffice già aperto.

Se non c'è nessuno dei due si torna alla conversione interna (convert.py), che conserva il contenuto ma non
l'impaginazione: chi la chiama aggiunge un avviso.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from wmt_pdf.i18n import t
from wmt_pdf.source import CliError

ENGINES = ("auto", "office", "libreoffice", "interno")
TIMEOUT = 120  # secondi: un documento lungo con molte immagini può volerci; Office e poi LibreOffice al massimo il doppio

WORD = {".docx", ".docm", ".doc", ".rtf", ".odt", ".dotx"}
EXCEL = {".xlsx", ".xlsm", ".xls", ".ods"}
POWERPOINT = {".pptx", ".ppt", ".odp", ".ppsx"}
OFFICE_FORMATS = WORD | EXCEL | POWERPOINT
OPENDOCUMENT = {".odt", ".ods", ".odp"}
NEEDS_ENGINE = {".doc", ".docm", ".dotx", ".rtf", ".xls", ".ods", ".ppt", ".odp", ".ppsx"}  # niente conversione interna

# Uno script per programma, con i percorsi passati come variabili d'ambiente (mai dentro lo script): nessun nome di
# file può diventare codice PowerShell. 17 = wdExportFormatPDF, 0 = xlTypePDF, 32 = ppSaveAsPDF; AutomationSecurity
# 3 = macro sempre disattivate; la password finta (in Word anche quella di scrittura; Excel la rifiuta, ma aprendo in sola
# lettura non la chiede) fa fallire subito i documenti protetti
# invece di aprire una richiesta. Il numero del processo Office avviato apposta finisce in WPDF_OFFICE_PID: se il
# tempo scade lo si chiude (solo quello: mai un Office aperto dall'utente). L'errore esce in UTF-8, una riga sola.
_PS_WRAP = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ErrorActionPreference = 'Stop'; "
    "$in = $env:WPDF_OFFICE_IN; $out = $env:WPDF_OFFICE_OUT; $pw = 'wpdf-no-password'; $M = [Type]::Missing; "
    "$code = 0; $mine = @(); "
    "$before = @(Get-Process {proc} -ErrorAction SilentlyContinue | ForEach-Object {{ $_.Id }}); "
    "try {{ $app = New-Object -ComObject {progid}; "
    "$mine = @(Get-Process {proc} -ErrorAction SilentlyContinue | Where-Object {{ $before -notcontains $_.Id }}); "
    "if ($mine.Count -eq 1) {{ Set-Content -LiteralPath $env:WPDF_OFFICE_PID -Value $mine[0].Id }}; "
    "{body} }} catch {{ $e = $_.Exception; if ($e.InnerException) {{ $e = $e.InnerException }}; "
    r"[Console]::Error.WriteLine(($e.Message -replace '\s+', ' ')); $code = 1 }} "
    # Con un file rovinato Office può restare fermo su una richiesta nascosta e ignorare Quit: la copia avviata qui
    # (solo quella) si chiude comunque, dopo averle dato qualche secondo per uscire da sola.
    "finally {{ if ($mine.Count -eq 1) {{ Wait-Process -Id $mine[0].Id -Timeout 5 -ErrorAction SilentlyContinue; "
    "Stop-Process -Id $mine[0].Id -Force -ErrorAction SilentlyContinue }} }}; exit $code"
)
_PS = {
    "word": _PS_WRAP.format(proc="WINWORD", progid="Word.Application", body=(
        "$app.Visible = $false; $app.DisplayAlerts = 0; $app.AutomationSecurity = 3; "
        "try { $doc = $app.Documents.Open($in, $false, $true, $false, $pw, $pw, $false, $pw); $doc.ExportAsFixedFormat($out, 17); $doc.Close(0) } finally { try { $app.Quit(0) } catch { } }")),
    "excel": _PS_WRAP.format(proc="EXCEL", progid="Excel.Application", body=(
        "$app.Visible = $false; $app.DisplayAlerts = $false; $app.AutomationSecurity = 3; "
        "try { $book = $app.Workbooks.Open($in, 0, $true, $M, $pw); "
        "$book.ExportAsFixedFormat(0, $out); $book.Close($false) } finally { try { $app.Quit() } catch { } }")),
    # PowerPoint ha una sola copia per utente: se era già aperta (mine vuoto) la si lascia com'è, con le sue
    # presentazioni; si chiude solo quella avviata qui.
    "powerpoint": _PS_WRAP.format(proc="POWERPNT", progid="PowerPoint.Application", body=(
        "$app.AutomationSecurity = 3; "
        "try { $deck = $app.Presentations.Open($in + '::' + $pw, -1, 0, 0); $deck.SaveAs($out, 32); $deck.Close() } "
        "finally { if ($mine.Count -eq 1) { try { $app.Quit() } catch { } } }")),
}
_PROG_ID = {"word": "Word.Application", "excel": "Excel.Application", "powerpoint": "PowerPoint.Application"}


def _office_app(suffix: str) -> str:
    if suffix in WORD:
        return "word"
    if suffix in EXCEL:
        return "excel"
    return "powerpoint"


def office_available(app: str) -> bool:
    """Microsoft Office installato (Windows): la classe COM del programma è registrata."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{_PROG_ID[app]}\CLSID"):
            return True
    except OSError:
        return False


def _on_path(name: str) -> str | None:
    """Un eseguibile nelle cartelle del PATH, mai nella cartella corrente; su Windows solo .exe (non .bat o .cmd)."""
    names = [f"{name}.exe"] if sys.platform == "win32" else [name]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not Path(folder).is_absolute():
            continue
        for candidate in names:
            path = Path(folder) / candidate
            if path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK)):
                return str(path)
    return None


def find_libreoffice() -> str | None:
    """Il comando di LibreOffice: nel PATH oppure nelle cartelle in cui lo installa il suo programma d'installazione."""
    for name in ("soffice", "libreoffice"):
        found = _on_path(name)
        if found:
            return found
    places = []
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                places.append(Path(root) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        places.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    return next((str(p) for p in places if p.is_file()), None)


ENGINE_ENV = "WPDF_OFFICE_ENGINE"  # il motore di «auto» per questo computer (es. interno: mai programmi esterni)


def resolve_engine(engine: str = "auto") -> str:
    """Il motore da usare davvero: «auto» segue WPDF_OFFICE_ENGINE, se impostato."""
    if engine == "auto" and os.environ.get(ENGINE_ENV) in ENGINES:
        engine = os.environ[ENGINE_ENV]
    if engine not in ENGINES:
        raise CliError(t("motore di conversione sconosciuto: {engine} (usa {join})", engine=engine, join=", ".join(ENGINES)))
    return engine


def engines_for(suffix: str, engine: str = "auto") -> list[str]:
    """I motori da provare, nell'ordine: Office per i suoi formati, LibreOffice per OpenDocument (e come riserva)."""
    engine = resolve_engine(engine)
    if suffix not in OFFICE_FORMATS or engine == "interno":
        return []
    if engine == "office":
        return ["office"]
    if engine == "libreoffice":
        return ["libreoffice"]
    return ["libreoffice", "office"] if suffix in OPENDOCUMENT else ["office", "libreoffice"]


def faithful_pdf(path: Path, engine: str = "auto") -> tuple[bytes | None, list[str]]:
    """Il PDF fatto dal programma Office o da LibreOffice; None (con il motivo) se nessuno dei due è disponibile."""
    suffix = path.suffix.lower()
    notes: list[str] = []
    chosen = resolve_engine(engine)
    for name in engines_for(suffix, engine):
        if name == "office":
            app = _office_app(suffix)
            if not office_available(app):
                if chosen == "office":
                    raise CliError(t("Microsoft Office non è installato su questo computer"))
                continue
            try:
                return _run_office(app, path), notes
            except CliError as exc:
                notes.append(t("Microsoft Office non ha convertito il file: {error}", error=exc))
        else:
            soffice = find_libreoffice()
            if not soffice:
                if chosen == "libreoffice":
                    raise CliError(t("LibreOffice non è installato su questo computer"))
                continue
            try:
                return _run_libreoffice(soffice, path), notes
            except CliError as exc:
                notes.append(t("LibreOffice non ha convertito il file: {error}", error=exc))
    if notes and chosen in ("office", "libreoffice"):
        raise CliError(notes[-1])  # scelto apposta: niente ripiego sulla conversione interna
    return None, notes


def _powershell() -> str:
    """Il PowerShell di Windows per percorso completo: mai uno con lo stesso nome nella cartella corrente."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _run_office(app: str, path: Path) -> bytes:
    # ignore_cleanup_errors: se un Office rimasto appeso tiene aperto un file, l'errore resta quello vero.
    with tempfile.TemporaryDirectory(prefix="wpdf-office-", ignore_cleanup_errors=True) as tmp:
        out = Path(tmp) / "out.pdf"
        pid_file = Path(tmp) / "office.pid"
        env = os.environ | {"WPDF_OFFICE_IN": str(path.resolve()), "WPDF_OFFICE_OUT": str(out),
                            "WPDF_OFFICE_PID": str(pid_file)}
        command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS[app]]
        _run(command, env, office_pid=pid_file)
        return _read_pdf(out)


def _run_libreoffice(soffice: str, path: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix="wpdf-lo-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        # Copia con un nome semplice: LibreOffice deriva dal nome il file di uscita, e così non c'è nulla da indovinare.
        source = work / f"documento{path.suffix.lower()}"
        shutil.copyfile(path, source)
        profile = (work / "profilo").as_uri()
        command = [soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault", f"-env:UserInstallation={profile}",
                   "--convert-to", "pdf", "--outdir", str(work / "pdf"), str(source)]
        _run(command, os.environ.copy())
        return _read_pdf(work / "pdf" / "documento.pdf")


def _run(command: list[str], env: dict, office_pid: Path | None = None) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # nessuna finestra nera su Windows
    try:
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=flags)
    except OSError as exc:
        raise CliError(str(exc)) from None
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        _stop(process, office_pid)
        raise CliError(t("tempo scaduto dopo {n} secondi (forse il programma chiedeva qualcosa)", n=TIMEOUT)) from None
    if process.returncode != 0:
        detail = (stderr or stdout or b"").decode("utf-8", "replace").strip().splitlines()
        raise CliError(detail[-1] if detail else t("codice di uscita {code}", code=process.returncode))


def _stop(process: subprocess.Popen, office_pid: Path | None) -> None:
    """Tempo scaduto: si chiudono il comando e i processi che ha avviato, e nient'altro (indicati per numero).

    Office parte da DCOM, non come figlio di PowerShell: il suo numero lo scrive lo script. LibreOffice su Windows
    lancia soffice.bin da soffice.exe: si chiude l'albero del processo avviato qui.
    """
    pids = [process.pid]
    if office_pid and office_pid.is_file():
        try:
            pids.append(int(office_pid.read_text(encoding="utf-8", errors="replace").strip()))
        except ValueError:
            pass
    for pid in pids:
        if sys.platform == "win32":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/PID", str(pid), "/T", "/F"], capture_output=True, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif pid == process.pid:
            process.kill()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _read_pdf(out: Path) -> bytes:
    if not out.is_file() or out.stat().st_size == 0:
        raise CliError(t("nessun PDF prodotto"))
    data = out.read_bytes()
    if not data.startswith(b"%PDF"):
        raise CliError(t("il file prodotto non è un PDF"))
    return data
