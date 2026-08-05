# pdfdelta [![PyPI version](https://img.shields.io/pypi/v/pdfdelta)](https://pypi.org/project/pdfdelta/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/pdfdelta/)

pdfdelta: visual PDF diff for academic papers

**pdfdelta** compares two academic paper PDFs and highlights deletions on the old PDF and additions on the new PDF while preserving the original page layout. It is built for paper revision review, especially arXiv updates, camera-ready drafts, advisor edits, coauthor edits, and LaTeX-generated PDFs.

```sh
pip install pdfdelta
pdfdelta old.pdf new.pdf
```

This writes two annotated PDFs in the current directory:

- `old_marked.pdf` - the old PDF with deletions highlighted
- `new_marked.pdf` - the new PDF with additions highlighted

<p align="center">
  <img src="https://raw.githubusercontent.com/mli55/pdfdelta/main/examples/old_marked.png" alt="Old PDF with deletions highlighted" width="48%" />
  <img src="https://raw.githubusercontent.com/mli55/pdfdelta/main/examples/new_marked.png" alt="New PDF with additions highlighted" width="48%" />
</p>

## Good For

- Comparing arXiv and camera-ready paper revisions
- Checking advisor or coauthor edits
- Reviewing LaTeX-generated PDFs
- Finding small wording changes without being distracted by layout or reflow

## Why pdfdelta?

- **Text diff** loses page layout and makes it harder to review visual change tracking in context.
- **Image diff** can be too sensitive to tiny rendering changes, antialiasing, or page rasterization differences.
- **latexdiff** requires LaTeX source and may not work for arbitrary PDFs.
- **Acrobat-style comparison** is often heavyweight, proprietary, or harder to automate from the command line.

pdfdelta is a lightweight CLI for visual PDF diff, PDF comparison, document comparison, and LaTeX PDF diff workflows where the PDF itself is the review artifact.

## Usage

### Options

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--old-out` | `old_marked.pdf` | Output path for the annotated old PDF |
| `--new-out` | `new_marked.pdf` | Output path for the annotated new PDF |
| `--opacity` | `0.35` | Highlight opacity from `0.0` to `1.0` |

### Command-Line Example

```sh
pdfdelta examples/old.pdf examples/new.pdf \
  --old-out examples/old_marked.pdf \
  --new-out examples/new_marked.pdf
```

To install directly from the repository:

```sh
pip install git+https://github.com/mli55/pdfdelta.git
```

### Python API Example

The CLI is the primary interface. pdfdelta also exposes low-level functions if you want to build your own comparison or annotation flow:

```python
from pdfdelta.annotate import apply_annotations
from pdfdelta.compare import compare_documents
from pdfdelta.extract import extract_document

old_pages = extract_document("old.pdf")
new_pages = extract_document("new.pdf")

old_rects, new_rects = compare_documents(old_pages, new_pages)

apply_annotations("old.pdf", "old_marked.pdf", old_rects, color=(1.0, 0.0, 0.0))
apply_annotations("new.pdf", "new_marked.pdf", new_rects, color=(0.0, 1.0, 0.0))
```

## Limitations

pdfdelta is intended for PDFs with extractable text, such as PDFs generated from LaTeX, Word, or other publishing tools. It is not designed for scanned PDFs, OCR-heavy documents, or image-only pages unless the text layer is accurate enough for comparison.

Please open an issue if you see bad alignment, missing highlights, unexpected highlights, weird page layouts, or a PDF comparison case that should work for academic paper revisions but does not.

## How It Works

```text
 old.pdf    new.pdf
   |           |
   v           v
 Extract words with PyMuPDF word text + bounding boxes
   |
   v
 Global diff across flattened pages
   |
   v
 Word-level and sub-word diff
   |
   v
 Move filter to suppress reflow noise across pages and columns,
 and same-page block moves (e.g. swapped subfigures, repeated
 figure labels)
   |
   v
 Annotate original PDFs
   |
   v
 old_marked.pdf
 new_marked.pdf
```

## Changelog

### 0.1.4

- Fixed false-positive highlights on repeated short labels (figure axis text, tick numbers, running headers): they could be marked as changed even when byte-identical, whenever other copies of the same text were added or removed elsewhere in the document.
- Blocks that merely moved on the same page (for example swapped subfigures) are no longer highlighted; only genuine text edits are marked.

## License

MIT
