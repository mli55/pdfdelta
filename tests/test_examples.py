from pathlib import Path

from pdfdelta.compare import compare_documents
from pdfdelta.extract import extract_document


def test_example_annotation_counts_match_current_algorithm() -> None:
    root = Path(__file__).resolve().parents[1]

    old_pages = extract_document(str(root / "examples" / "old.pdf"))
    new_pages = extract_document(str(root / "examples" / "new.pdf"))

    old_rects, new_rects = compare_documents(old_pages, new_pages)

    assert sum(len(rects) for rects in old_rects.values()) == 6
    assert sum(len(rects) for rects in new_rects.values()) == 5
