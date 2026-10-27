"""Documenti Office impaginati da Microsoft Office o da LibreOffice (simulati: i test girano ovunque)."""

import os
from pathlib import Path

import pytest

from wmt_pdf import office
from wmt_pdf.convert import to_pdf
from wmt_pdf.source import CliError

MINI_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj " \
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture
def docx_file(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "lettera; & $(rm).docx"  # nome ostile: non deve mai finire dentro un comando
    document = docx.Document()
    document.add_paragraph("Testo della lettera")
    document.save(path)
    return path


@pytest.fixture
def engines(monkeypatch):
    """Nessun motore installato, finché il test non ne «installa» uno; il comando eseguito resta in calls."""
    monkeypatch.delenv(office.ENGINE_ENV, raising=False)
    state = {"office": False, "libreoffice": None, "calls": [], "fail": False}
    monkeypatch.setattr(office, "office_available", lambda app: state["office"])
    monkeypatch.setattr(office, "find_libreoffice", lambda: state["libreoffice"])

    class FakePopen:
        def __init__(self, command, env=None, **kwargs):
            state["calls"].append((command, env))
            self.pid, self.command, self.env = 4242, command, env

        def communicate(self, timeout=None):
            if state["fail"]:
                self.returncode = 1
                return b"", b"Errore: documento rovinato"
            if self.command[0].lower().endswith("powershell.exe"):
                Path(self.env["WPDF_OFFICE_OUT"]).write_bytes(MINI_PDF)
            else:
                outdir = Path(self.command[self.command.index("--outdir") + 1])
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "documento.pdf").write_bytes(MINI_PDF)
            self.returncode = 0
            return b"", b""

    monkeypatch.setattr(office.subprocess, "Popen", FakePopen)
    return state


def test_engine_order(monkeypatch):
    monkeypatch.delenv(office.ENGINE_ENV, raising=False)
    assert office.engines_for(".docx") == ["office", "libreoffice"]
    assert office.engines_for(".odt") == ["libreoffice", "office"]
    assert office.engines_for(".docx", "libreoffice") == ["libreoffice"]
    assert office.engines_for(".docx", "interno") == []
    assert office.engines_for(".md") == []
    with pytest.raises(CliError, match="sconosciuto"):
        office.engines_for(".docx", "stampante")


def test_environment_chooses_auto(monkeypatch):
    monkeypatch.setenv(office.ENGINE_ENV, "libreoffice")
    assert office.engines_for(".docx") == ["libreoffice"]
    assert office.engines_for(".docx", "office") == ["office"]  # una scelta esplicita vale sull'ambiente


def test_microsoft_office_paths_only_in_environment(engines, docx_file):
    engines["office"] = True
    converted = to_pdf(docx_file)
    assert converted.data == MINI_PDF and converted.notes == []
    command, env = engines["calls"][0]
    assert Path(command[0]).parts[-4:] == ("System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    assert command[1] == "-NoProfile"
    assert "lettera" not in " ".join(command)  # il nome del file passa solo dall'ambiente
    assert env["WPDF_OFFICE_IN"] == str(docx_file.resolve())
    assert "AutomationSecurity = 3" in command[-1]  # macro disattivate


def test_libreoffice_with_private_profile(engines, docx_file):
    engines["libreoffice"] = "soffice"
    converted = to_pdf(docx_file)
    assert converted.data == MINI_PDF
    command, _ = engines["calls"][0]
    assert "--headless" in command and any(a.startswith("-env:UserInstallation=file:") for a in command)
    assert command[-1].endswith("documento.docx")  # una copia con un nome semplice, non il file originale


def test_explicit_engine_missing_is_an_error(engines, docx_file):
    with pytest.raises(CliError, match="Microsoft Office non è installato"):
        to_pdf(docx_file, engine="office")
    with pytest.raises(CliError, match="LibreOffice non è installato"):
        to_pdf(docx_file, engine="libreoffice")


def test_failure_falls_back_with_notes(engines, docx_file):
    pytest.importorskip("mammoth")
    engines["office"] = True
    engines["fail"] = True
    converted = to_pdf(docx_file)
    assert converted.data.startswith(b"%PDF") and converted.data != MINI_PDF
    assert any("documento rovinato" in n for n in converted.notes)
    assert any("impaginazione semplificata" in n for n in converted.notes)


def test_no_engine_simplified_with_notice(engines, docx_file):
    pytest.importorskip("mammoth")
    converted = to_pdf(docx_file)
    assert any("impaginazione semplificata" in n for n in converted.notes)
    assert engines["calls"] == []
    assert to_pdf(docx_file, engine="interno").notes == []  # scelto apposta: nessun avviso


def test_old_formats_need_an_engine(engines, tmp_path):
    doc = tmp_path / "vecchio.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0 finto documento Word 97")
    with pytest.raises(CliError, match="Microsoft Office o LibreOffice"):
        to_pdf(doc)
    engines["libreoffice"] = "soffice"
    assert to_pdf(doc).data == MINI_PDF


def test_attachments_never_use_external_programs(engines, docx_file):
    pytest.importorskip("mammoth")
    engines["office"] = True
    to_pdf(docx_file, local_files=False)
    assert engines["calls"] == []


@pytest.mark.skipif(not os.environ.get("WPDF_TEST_OFFICE"), reason="solo a mano: WPDF_TEST_OFFICE=1 con Word installato")
def test_real_microsoft_word(docx_file, monkeypatch):
    monkeypatch.delenv(office.ENGINE_ENV, raising=False)
    if not office.office_available("word"):
        pytest.skip("Word non installato")
    data = to_pdf(docx_file, engine="office").data
    assert data.startswith(b"%PDF")


def test_explicit_engine_failure_is_an_error(engines, docx_file):
    engines["office"] = True
    engines["fail"] = True
    with pytest.raises(CliError, match="documento rovinato"):
        to_pdf(docx_file, engine="office")
