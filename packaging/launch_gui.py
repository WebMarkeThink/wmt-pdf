"""Avvio dell'applicazione (finestra, senza console): equivale a `wpdf gui`."""

import sys

from wmt_pdf.cli import main

if __name__ == "__main__":
    sys.exit(main(["gui"]))
