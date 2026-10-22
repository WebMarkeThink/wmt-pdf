# PyInstaller: una sola analisi, due programmi che condividono le librerie.
#   wmt-pdf  finestra dell'interfaccia (senza console)
#   wpdf     comando per il terminale e per Claude Code
# Su macOS i due finiscono in wmt-pdf.app (Contents/MacOS/).
#
# Uso: uv run --group build pyinstaller --noconfirm packaging/wmt-pdf.spec
# (prima: uv run --group build python packaging/make_icons.py)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

HERE = Path(SPECPATH)  # noqa: F821 - definita da PyInstaller
ICONS = HERE / "build" / "icons"
MAC = sys.platform == "darwin"

datas, binaries, hidden = [], [], collect_submodules("wmt_pdf")
# Pacchetti con file di dati o librerie native che l'analisi da sola non vede tutti.
for package in ("wmt_pdf", "pypdfium2", "pypdfium2_raw", "rapidocr", "xhtml2pdf", "reportlab", "pyhanko",
                "pyhanko_certvalidator", "webview", "pikepdf", "pdfplumber", "pdfminer"):
    try:
        d, b, h = collect_all(package)
    except Exception:  # extra non installato: si costruisce lo stesso
        continue
    datas += d
    binaries += b
    hidden += h
for dist in ("wmt-pdf", "pyhanko", "pikepdf", "rapidocr", "pywebview"):
    try:
        datas += copy_metadata(dist)  # versioni lette con importlib.metadata
    except Exception:
        pass

a = Analysis(  # noqa: F821
    [str(HERE / "launch_cli.py"), str(HERE / "launch_gui.py")],
    pathex=[str(HERE.parent / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821


def scripts_without(name):
    """Gli script di avvio di PyInstaller più uno solo dei due nostri."""
    return [s for s in a.scripts if s[0] != name]


icon = str(ICONS / ("wmt-pdf.icns" if MAC else "wmt-pdf.ico"))
gui = EXE(  # noqa: F821
    pyz, scripts_without("launch_cli"), [], exclude_binaries=True, name="wmt-pdf", console=False, icon=icon,
    argv_emulation=False,
)
cli = EXE(  # noqa: F821
    pyz, scripts_without("launch_gui"), [], exclude_binaries=True, name="wpdf", console=True, icon=icon,
)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="wmt-pdf")  # noqa: F821

if MAC:
    from wmt_pdf import __version__

    app = BUNDLE(  # noqa: F821
        coll,
        name="wmt-pdf.app",
        icon=icon,
        bundle_identifier="it.webmarkethink.wmt-pdf",
        version=__version__,
        info_plist={
            "CFBundleName": "wmt-pdf",
            "CFBundleDisplayName": "wmt-pdf",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "15.0",  # pikepdf: pacchetti per macOS 15 o successivo
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PDF",
                "CFBundleTypeRole": "Editor",
                "LSItemContentTypes": ["com.adobe.pdf"],
                "LSHandlerRank": "Alternate",
            }],
        },
    )
