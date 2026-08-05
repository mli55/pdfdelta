from pdfdelta.compare import compare_documents
from pdfdelta.models import LineBox, PageBox, WordBox


def _word(page: int, line: int, index: int, text: str) -> WordBox:
    x0 = float(index * 10)
    y0 = float(line * 10)
    return WordBox(
        page_index=page,
        line_index=line,
        word_index=index,
        text=text,
        norm=text.lower(),
        rect=(x0, y0, x0 + 8.0, y0 + 8.0),
    )


def _line(page: int, line: int, words: list[str]) -> LineBox:
    word_boxes = [_word(page, line, i, word) for i, word in enumerate(words)]
    return LineBox(
        page_index=page,
        line_index=line,
        text=" ".join(words),
        norm_text=" ".join(word.lower() for word in words),
        words=word_boxes,
    )


def _pages(doc: list[list[list[str]]]) -> list[PageBox]:
    return [
        PageBox(
            page_index=p,
            lines=[_line(p, i, words) for i, words in enumerate(lines)],
        )
        for p, lines in enumerate(doc)
    ]


def _rect_count(page_to_rects: dict[int, list[tuple[float, float, float, float]]]) -> int:
    return sum(len(rects) for rects in page_to_rects.values())


# Figure-label lines: short, and repeated on another page in BOTH docs,
# so the unique-line move detection cannot rescue them.
LABELS = [["x", "(meter)"], ["y", "(meter)"], ["legend", "gt."]]

# Body text that sits next to the labels and acts as the diff anchor.
BODY = [
    ["the", "fusion", "module", "and", "decoder"],
    ["are", "trained", "end", "to", "end"],
    ["with", "a", "lightweight", "regularizer", "term"],
]

# An unrelated page identical in both docs.
FILLER = [
    ["unrelated", "page", "keeps", "alignment"],
    ["stable", "between", "both", "documents"],
    ["so", "only", "page", "zero", "differs"],
    ["in", "block", "ordering", "here"],
]


def test_same_page_block_swap_is_not_flagged() -> None:
    """A figure block and its surrounding body swap extraction order.

    Mirrors a float relocation: the labels are unchanged and stay on the
    same page, but the monotonic global diff crosses them out because the
    body anchor matches first.  Same-page move rounds must suppress them.
    """
    old = _pages([BODY + LABELS, FILLER, LABELS])
    new = _pages([LABELS + BODY, FILLER, LABELS])

    old_rects, new_rects = compare_documents(old, new)

    assert old_rects == {}
    assert new_rects == {}


def test_true_deletion_of_repeated_labels_stays_flagged() -> None:
    """One of two identical label blocks is genuinely removed.

    Move suppression must not swallow it: the surviving copy pairs up,
    the deleted copy keeps its highlight.
    """
    old = _pages([LABELS + BODY + LABELS])
    new = _pages([LABELS + BODY])

    old_rects, new_rects = compare_documents(old, new)

    assert new_rects == {}
    # One merged rect per deleted label line.
    assert _rect_count(old_rects) == 3
