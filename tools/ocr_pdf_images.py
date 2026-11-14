#!/usr/bin/env python3
"""L1 text for an image-only PDF by OCR of its embedded images, as a recorded command.

  python3 tools/ocr_pdf_images.py <L0.pdf> <L1.txt>

Extracts each embedded image unaltered (`pdfimages -png`), OCRs each in order with
`tesseract <image> - -l eng --psm 3` under OMP_THREAD_LIMIT=1 (single-threaded, so the
output is reproducible), and joins the pages with a form feed, as pdftotext does.
The same tesseract version and language data yield the same bytes; the version is
recorded in the manifest entry's extraction.tool.
"""
import glob, os, subprocess, sys, tempfile


def tesseract_version():
    p = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
    return (p.stdout or p.stderr).splitlines()[0].strip()


def ocr(pdf):
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdfimages", "-png", pdf, os.path.join(td, "img")], check=True)
        pages = []
        for img in sorted(glob.glob(os.path.join(td, "img-*.png"))):
            p = subprocess.run(["tesseract", img, "-", "-l", "eng", "--psm", "3"],
                               capture_output=True, check=True, env=env)
            pages.append(p.stdout)
    return b"\f".join(pages)


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    text = ocr(argv[1])
    with open(argv[2], "wb") as f:
        f.write(text)


if __name__ == "__main__":
    main(sys.argv)
