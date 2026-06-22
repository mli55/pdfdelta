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


def _page(lines: list[list[str]]) -> PageBox:
    return PageBox(
        page_index=0,
        lines=[_line(0, i, words) for i, words in enumerate(lines)],
    )


def _rect_count(page_to_rects: dict[int, list[tuple[float, float, float, float]]]) -> int:
    return sum(len(rects) for rects in page_to_rects.values())


def test_line_break_hyphenation_is_not_marked_as_change() -> None:
    old_pages = [_page([["fixed", "or", "selected", "from", "the", "codebook"]])]
    new_pages = [_page([["fixed", "or", "se-"], ["lected", "from", "the", "codebook"]])]

    old_rects, new_rects = compare_documents(old_pages, new_pages)

    assert _rect_count(old_rects) == 0
    assert _rect_count(new_rects) == 0


def test_real_insertions_survive_near_hyphenation_reflow() -> None:
    old_pages = [_page([["fixed", "or", "selected", "from", "the", "codebook"]])]
    new_pages = [
        _page([["fixed", "or", "se-"], ["lected", "from", "the", "large", "codebook"]])
    ]

    old_rects, new_rects = compare_documents(old_pages, new_pages)

    assert _rect_count(old_rects) == 0
    assert _rect_count(new_rects) == 1


def test_compound_word_split_at_line_end_matches_unsplit_form() -> None:
    old_pages = [_page([["into", "the", "contact-"], ["admission", "latency"]])]
    new_pages = [_page([["into", "the", "contact-admission", "latency"]])]

    old_rects, new_rects = compare_documents(old_pages, new_pages)

    assert _rect_count(old_rects) == 0
    assert _rect_count(new_rects) == 0


def test_minus_sign_changes_are_still_marked() -> None:
    old_pages = [_page([["value", "-1"]])]
    new_pages = [_page([["value", "1"]])]

    old_rects, new_rects = compare_documents(old_pages, new_pages)

    assert _rect_count(old_rects) == 1
    assert _rect_count(new_rects) == 1
