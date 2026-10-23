"""Avvio dell'applicazione (finestra, senza console): equivale a `wpdf gui [FILE]`.

Windows passa il file su cui si è fatto doppio clic come primo argomento.
"""

import sys

from wmt_pdf.cli import main

if __name__ == "__main__":
    sys.exit(main(["gui", "--", *sys.argv[1:2]]))  # «--»: un file che inizia con «-» non è un'opzione
