from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import DefaultDict

from .models import LineBox, PageBox, RectTuple, WordBox


@dataclass(frozen=True)
class DiffUnit:
    norm: str
    words: tuple[WordBox, ...]


def _fold_hyphens(norm: str) -> str:
    """Normalize word hyphen variants without touching minus signs."""
    return re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", "", norm)


def _word_diff_units(words: list[WordBox]) -> list[DiffUnit]:
    """Build word-diff units with line-break hyphenation folded.

    A PDF line break can expose one logical word as two extracted words,
    e.g. ``"se-"`` + ``"lected"``.  Compare that as ``"selected"`` while
    keeping the original word boxes so annotations still land precisely.
    Word-internal hyphens are folded in the comparison key so a compound
    split at a line end (``"contact-"`` + ``"admission"``) matches its
    unsplit form (``"contact-admission"``), without treating minus signs as
    word hyphens.
    """
    units: list[DiffUnit] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word.norm.endswith("-") and len(word.norm) > 1 and i + 1 < len(words):
            next_word = words[i + 1]
            joined = word.norm[:-1] + next_word.norm
            units.append(
                DiffUnit(
                    norm=_fold_hyphens(joined),
                    words=(word, next_word),
                )
            )
            i += 2
            continue

        units.append(DiffUnit(norm=_fold_hyphens(word.norm), words=(word,)))
        i += 1

    return units


def _sub_word_rect(
    old_word: WordBox, new_word: WordBox,
) -> tuple[RectTuple, RectTuple]:
    """Narrow a 1:1 word replacement to only the changed characters.

    Estimates left/right boundaries proportionally based on the shared
    prefix and suffix lengths.  Falls back to the full word rects when
    the words are completely different.
    """
    a, b = old_word.norm, new_word.norm
    # Find common prefix length
    prefix = 0
    while prefix < len(a) and prefix < len(b) and a[prefix] == b[prefix]:
        prefix += 1
    # Find common suffix length (don't overlap with prefix)
    suffix = 0
    while (
        suffix < len(a) - prefix
        and suffix < len(b) - prefix
        and a[-(suffix + 1)] == b[-(suffix + 1)]
    ):
        suffix += 1

    if prefix == 0 and suffix == 0:
        return old_word.rect, new_word.rect

    def _trim(rect: RectTuple, text_len: int) -> RectTuple:
        if text_len == 0:
            return rect
        x0, y0, x1, y1 = rect
        w = x1 - x0
        new_x0 = x0 + w * (prefix / text_len)
        new_x1 = x1 - w * (suffix / text_len)
        if new_x0 >= new_x1:
            return rect
        return (new_x0, y0, new_x1, y1)

    return _trim(old_word.rect, len(a)), _trim(new_word.rect, len(b))


def _chunk_word_diff(
    old_chunk: list[LineBox],
    new_chunk: list[LineBox],
) -> tuple[list[RectTuple], list[RectTuple]]:
    """Flatten words across a multi-line chunk and do word-level diff.

    This handles text reflow: when line breaks shift but the actual words
    are mostly the same, only the truly changed words get highlighted.
    """
    old_words = [w for line in old_chunk for w in line.words]
    new_words = [w for line in new_chunk for w in line.words]

    old_units = _word_diff_units(old_words)
    new_units = _word_diff_units(new_words)
    old_tokens = [u.norm for u in old_units]
    new_tokens = [u.norm for u in new_units]

    sm = SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    old_rects: list[RectTuple] = []
    new_rects: list[RectTuple] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if (
            tag == "replace"
            and (i2 - i1) == 1
            and (j2 - j1) == 1
            and len(old_units[i1].words) == 1
            and len(new_units[j1].words) == 1
        ):
            # Single word replaced by single word — use sub-word precision
            o_r, n_r = _sub_word_rect(old_units[i1].words[0], new_units[j1].words[0])
            old_rects.append(o_r)
            new_rects.append(n_r)
        else:
            if tag in ("delete", "replace"):
                old_rects.extend(w.rect for u in old_units[i1:i2] for w in u.words)
            if tag in ("insert", "replace"):
                new_rects.extend(w.rect for u in new_units[j1:j2] for w in u.words)

    return old_rects, new_rects


def _merge_opcodes(opcodes: list[tuple]) -> list[tuple]:
    """Merge adjacent delete/insert pairs into replace blocks.

    SequenceMatcher often emits a delete immediately followed by an insert
    (or vice-versa) for text that simply reflowed across lines.  Merging
    them into a single 'replace' lets _chunk_word_diff handle the reflow
    and only highlight truly changed words.
    """
    merged: list[tuple] = []
    for op in opcodes:
        if not merged:
            merged.append(op)
            continue
        prev_tag, pi1, pi2, pj1, pj2 = merged[-1]
        tag, i1, i2, j1, j2 = op
        # Merge delete+insert or insert+delete into replace
        if (
            {prev_tag, tag} == {"delete", "insert"}
            or (prev_tag == "replace" and tag in ("delete", "insert"))
            or (tag == "replace" and prev_tag in ("delete", "insert"))
        ) and pi2 == i1 and pj2 == j1:
            merged[-1] = ("replace", pi1, i2, pj1, j2)
        else:
            merged.append(op)
    return merged


def _same_line(a: RectTuple | list[float], b: RectTuple) -> bool:
    """Check if two rects are on the same text line (>50% y overlap)."""
    y_overlap = min(a[3], b[3]) - max(a[1], b[1])
    y_height = max(a[3] - a[1], b[3] - b[1])
    return y_height > 0 and y_overlap / y_height > 0.5


def merge_nearby_rects(rects: list[RectTuple], x_gap: float = 10.0) -> list[RectTuple]:
    """Merge horizontally adjacent rects that share the same line.

    Consecutive highlighted word rects on the same line are combined
    into a single wide rect.  x_gap should be at least as large as
    the normal word spacing in the PDF (~3-9 pt typically).
    """
    if not rects:
        return []

    # Sort by vertical midpoint then left edge.
    sorted_rects = sorted(rects, key=lambda r: ((r[1] + r[3]) / 2, r[0]))

    merged: list[list[float]] = [list(sorted_rects[0])]
    for r in sorted_rects[1:]:
        prev = merged[-1]
        # gap > 0 means r starts after prev ends; gap < 0 means overlap/wrap.
        # Only merge when gap is within [-x_gap, x_gap] to prevent merging
        # across columns (where r[0] << prev[2] due to column switch).
        gap = r[0] - prev[2]
        if _same_line(prev, r) and -x_gap <= gap <= x_gap:
            prev[0] = min(prev[0], r[0])
            prev[2] = max(prev[2], r[2])
            prev[1] = min(prev[1], r[1])
            prev[3] = max(prev[3], r[3])
        else:
            merged.append(list(r))

    return [(m[0], m[1], m[2], m[3]) for m in merged]


def dedupe_rects(rects: list[RectTuple], ndigits: int = 1) -> list[RectTuple]:
    seen = set()
    out: list[RectTuple] = []
    for r in rects:
        key = tuple(round(v, ndigits) for v in r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _dehyphenate_norms(norms: list[str]) -> list[tuple[str, list[int]]]:
    """Join hyphenated word pairs into single tokens.

    PDF line-break hyphenation produces e.g. ``"aver-", "age"`` which
    should be treated as ``"average"`` for matching purposes.

    Returns ``[(dehyphenated_norm, [original_indices]), ...]``.
    """
    result: list[tuple[str, list[int]]] = []
    i = 0
    while i < len(norms):
        if norms[i].endswith('-') and len(norms[i]) > 1 and i + 1 < len(norms):
            joined = norms[i][:-1] + norms[i + 1]
            result.append((joined, [i, i + 1]))
            i += 2
        else:
            result.append((norms[i], [i]))
            i += 1
    return result


def _is_hyph_match(a: str, b: str) -> bool:
    """Check if *a* and *b* are plausibly the same word split by hyphenation.

    Detected patterns:
    - ``"aver-"`` vs ``"average"``  (line-break hyphen on one side)
    - ``"particularly"`` vs ``"ticularly"``  (continuation of ``"par-"``
      on the previous page)
    """
    # Pattern 1: one ends with '-', the other starts with that prefix.
    if a.endswith('-') and len(a) > 2:
        prefix = a[:-1]
        if b.startswith(prefix) and len(b) > len(prefix):
            return True
    if b.endswith('-') and len(b) > 2:
        prefix = b[:-1]
        if a.startswith(prefix) and len(a) > len(prefix):
            return True
    # Pattern 2: one token is a suffix of the other (the "continuation"
    # half of a hyphenated word whose first half sits on the prev page).
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and longer.endswith(shorter) and len(shorter) >= len(longer) * 0.5:
        return True
    return False


def compare_documents(
    old_pages: list[PageBox],
    new_pages: list[PageBox],
) -> tuple[dict[int, list[RectTuple]], dict[int, list[RectTuple]]]:
    """Compare two documents using a global (cross-page) diff.

    Flattens all lines from all pages, performs a single global diff,
    then maps highlighted rects back to their source pages.  This
    correctly handles text that reflowed across page boundaries.

    Lines whose normalized text appears exactly once in each document
    are recognised as *moved* (page reflow) and excluded from the diff
    so that only genuinely changed text gets highlighted.
    """
    # Flatten: list of (page_index, LineBox)
    old_flat: list[tuple[int, LineBox]] = [
        (p.page_index, line) for p in old_pages for line in p.lines
    ]
    new_flat: list[tuple[int, LineBox]] = [
        (p.page_index, line) for p in new_pages for line in p.lines
    ]

    old_texts = [line.norm_text for _, line in old_flat]
    new_texts = [line.norm_text for _, line in new_flat]

    # Frequency counts for move detection: a line appearing exactly once
    # in each document is the same line, just at a different position.
    old_counts = Counter(old_texts)
    new_counts = Counter(new_texts)
    new_text_set = set(new_texts)
    old_text_set = set(old_texts)

    def _is_moved(text: str) -> bool:
        """True when *text* appears exactly once in each document."""
        return old_counts[text] == 1 and new_counts[text] == 1

    sm = SequenceMatcher(a=old_texts, b=new_texts, autojunk=False)
    opcodes = _merge_opcodes(list(sm.get_opcodes()))

    # ── First pass: collect candidate highlight words ────────────────
    # Each candidate is (WordBox, RectTuple, is_subword).
    # is_subword=True means the rect was trimmed by _sub_word_rect and
    # represents a genuine character-level change (never suppress these).
    old_cands: list[tuple[WordBox, RectTuple, bool]] = []
    new_cands: list[tuple[WordBox, RectTuple, bool]] = []

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue

        if tag == "delete":
            for idx in range(i1, i2):
                _, line = old_flat[idx]
                if line.norm_text in new_text_set and _is_moved(line.norm_text):
                    continue
                for w in line.words:
                    old_cands.append((w, w.rect, False))
            continue

        if tag == "insert":
            for idx in range(j1, j2):
                _, line = new_flat[idx]
                if line.norm_text in old_text_set and _is_moved(line.norm_text):
                    continue
                for w in line.words:
                    new_cands.append((w, w.rect, False))
            continue

        # tag == "replace" — pre-filter moved lines, then word-level diff
        old_chunk_texts = set(old_texts[i1:i2])
        new_chunk_texts = set(new_texts[j1:j2])

        moved_old = set()
        for idx in range(i1, i2):
            t = old_texts[idx]
            if t not in new_chunk_texts and t in new_text_set and _is_moved(t):
                moved_old.add(idx)

        moved_new = set()
        for idx in range(j1, j2):
            t = new_texts[idx]
            if t not in old_chunk_texts and t in old_text_set and _is_moved(t):
                moved_new.add(idx)

        old_words = [w for i, (_, line) in enumerate(old_flat[i1:i2], i1)
                     if i not in moved_old for w in line.words]
        new_words = [w for i, (_, line) in enumerate(new_flat[j1:j2], j1)
                     if i not in moved_new for w in line.words]

        if not old_words and not new_words:
            continue

        old_units = _word_diff_units(old_words)
        new_units = _word_diff_units(new_words)
        old_norms = [u.norm for u in old_units]
        new_norms = [u.norm for u in new_units]

        sm2 = SequenceMatcher(a=old_norms, b=new_norms, autojunk=False)
        for op_tag, oi1, oi2, oj1, oj2 in sm2.get_opcodes():
            if op_tag == "equal":
                continue
            if (
                op_tag == "replace"
                and (oi2 - oi1) == 1
                and (oj2 - oj1) == 1
                and len(old_units[oi1].words) == 1
                and len(new_units[oj1].words) == 1
            ):
                old_word = old_units[oi1].words[0]
                new_word = new_units[oj1].words[0]
                o_r, n_r = _sub_word_rect(old_word, new_word)
                old_cands.append((old_word, o_r, o_r != old_word.rect))
                new_cands.append((new_word, n_r, n_r != new_word.rect))
            else:
                if op_tag in ("delete", "replace"):
                    for unit in old_units[oi1:oi2]:
                        for w in unit.words:
                            old_cands.append((w, w.rect, False))
                if op_tag in ("insert", "replace"):
                    for unit in new_units[oj1:oj2]:
                        for w in unit.words:
                            new_cands.append((w, w.rect, False))

    # ── Second pass: same-page / page-boundary reflow suppression ──
    # For each pair of nearby pages (old_pg P, new_pg P+δ, δ ∈ {0, ±1}),
    # compare ALL words from both pages using dehyphenation-aware
    # normalization.  Candidate words within contiguous matching runs of
    # ≥ MIN_MATCH tokens are recognised as reflowed text and suppressed.
    # δ=0 matters too: repeated short lines (figure axis labels, running
    # headers) can be mis-paired by the global line diff even when they
    # sit unchanged on the same page, whenever other copies of the same
    # text were genuinely added or removed elsewhere in the document.
    #
    # Within-page dehyphenation joins e.g. "aver-"+"age" → "average".
    # Cross-page hyphenation (where a figure sits between the two halves)
    # is handled via _is_hyph_match tolerance on 1:1 replace blocks.
    MIN_MATCH = 2
    # Rounds of leftover re-matching per page pair (block-move detection).
    MAX_MOVE_ROUNDS = 8
    # Later rounds match blocks out of positional order, so a short run
    # ("used in the real-world") can pair with an unrelated occurrence
    # elsewhere on the page and punch holes in genuinely new text.
    # Demand stronger evidence there; real moved blocks (figure labels,
    # headers) are far longer than this.
    MOVE_MIN_MATCH = 5

    suppress_old: set[int] = set()
    suppress_new: set[int] = set()

    # Index: (page_index, word_rect) → candidate index
    old_cand_lookup: dict[tuple[int, RectTuple], int] = {}
    new_cand_lookup: dict[tuple[int, RectTuple], int] = {}
    old_cand_pages: set[int] = set()
    new_cand_pages: set[int] = set()

    for i, (w, _, sub) in enumerate(old_cands):
        if not sub:
            old_cand_lookup[(w.page_index, w.rect)] = i
            old_cand_pages.add(w.page_index)
    for i, (w, _, sub) in enumerate(new_cands):
        if not sub:
            new_cand_lookup[(w.page_index, w.rect)] = i
            new_cand_pages.add(w.page_index)

    def _all_words(pages: list[PageBox], pg: int) -> list[WordBox]:
        if 0 <= pg < len(pages):
            return [w for line in pages[pg].lines for w in line.words]
        return []

    def _suppress(dehyph, start, end, words, lookup, target_set):
        """Mark candidate words in dehyph[start:end] for suppression."""
        for k in range(start, end):
            for wi in dehyph[k][1]:
                w = words[wi]
                ci = lookup.get((w.page_index, w.rect))
                if ci is not None:
                    target_set.add(ci)

    checked: set[tuple[int, int]] = set()
    for old_pg in sorted(old_cand_pages):
        for delta in (0, 1, -1):
            new_pg = old_pg + delta
            if new_pg not in new_cand_pages:
                continue
            pair = (old_pg, new_pg)
            if pair in checked:
                continue
            checked.add(pair)

            old_words_pg = _all_words(old_pages, old_pg)
            new_words_pg = _all_words(new_pages, new_pg)
            if not old_words_pg or not new_words_pg:
                continue

            old_norms = [w.norm for w in old_words_pg]
            new_norms = [w.norm for w in new_words_pg]

            # Within-page dehyphenation only (no cross-page peek —
            # adjacent pages may start with figures, not text continuations)
            old_dehyph = _dehyphenate_norms(old_norms)
            new_dehyph = _dehyphenate_norms(new_norms)

            old_tokens = [t for t, _ in old_dehyph]
            new_tokens = [t for t, _ in new_dehyph]

            # Repeated monotonic matching = block-move detection.  A
            # single SequenceMatcher pass is order-preserving, so when
            # two blocks swap extraction order between versions (e.g. a
            # figure's labels vs the body text around it, after a float
            # relocates), only one of them can match.  After suppressing
            # the runs found in a round, remove them and rematch the
            # leftovers so displaced blocks get a chance to pair up.
            # Hyphenation edge-repair needs full positional context, so
            # it only runs in the first round.
            old_active = list(range(len(old_tokens)))
            new_active = list(range(len(new_tokens)))

            for round_no in range(MAX_MOVE_ROUNDS):
                min_run = MIN_MATCH if round_no == 0 else MOVE_MIN_MATCH
                a_toks = [old_tokens[k] for k in old_active]
                b_toks = [new_tokens[k] for k in new_active]

                sm_b = SequenceMatcher(a=a_toks, b=b_toks, autojunk=False)
                ops = list(sm_b.get_opcodes())

                matched_a: set[int] = set()
                matched_b: set[int] = set()

                for oi, (op, bi1, bi2, bj1, bj2) in enumerate(ops):
                    if op == "equal" and (bi2 - bi1) >= min_run:
                        for k in range(bi1, bi2):
                            _suppress(old_dehyph, old_active[k],
                                      old_active[k] + 1, old_words_pg,
                                      old_cand_lookup, suppress_old)
                        for k in range(bj1, bj2):
                            _suppress(new_dehyph, new_active[k],
                                      new_active[k] + 1, new_words_pg,
                                      new_cand_lookup, suppress_new)
                        matched_a.update(range(bi1, bi2))
                        matched_b.update(range(bj1, bj2))
                    elif op == "replace" and round_no == 0:
                        # Boundary tokens of a replace block next to a long
                        # equal run may be hyphenation artifacts.  E.g.
                        # "aver-" vs "average" or "particularly" vs "ticularly".
                        # Leading edge (preceded by equal ≥ MIN_MATCH)
                        if oi > 0:
                            p = ops[oi - 1]
                            if (p[0] == "equal" and (p[2] - p[1]) >= MIN_MATCH
                                    and _is_hyph_match(a_toks[bi1],
                                                       b_toks[bj1])):
                                _suppress(old_dehyph, old_active[bi1],
                                          old_active[bi1] + 1, old_words_pg,
                                          old_cand_lookup, suppress_old)
                                _suppress(new_dehyph, new_active[bj1],
                                          new_active[bj1] + 1, new_words_pg,
                                          new_cand_lookup, suppress_new)
                        # Trailing edge (followed by equal ≥ MIN_MATCH)
                        if oi < len(ops) - 1:
                            n = ops[oi + 1]
                            if (n[0] == "equal" and (n[2] - n[1]) >= MIN_MATCH
                                    and _is_hyph_match(a_toks[bi2 - 1],
                                                       b_toks[bj2 - 1])):
                                _suppress(old_dehyph, old_active[bi2 - 1],
                                          old_active[bi2 - 1] + 1, old_words_pg,
                                          old_cand_lookup, suppress_old)
                                _suppress(new_dehyph, new_active[bj2 - 1],
                                          new_active[bj2 - 1] + 1, new_words_pg,
                                          new_cand_lookup, suppress_new)

                if not matched_a:
                    break
                old_active = [k for i, k in enumerate(old_active)
                              if i not in matched_a]
                new_active = [k for i, k in enumerate(new_active)
                              if i not in matched_b]

    # ── Map surviving candidates to pages ────────────────────────────
    old_map: DefaultDict[int, list[RectTuple]] = defaultdict(list)
    new_map: DefaultDict[int, list[RectTuple]] = defaultdict(list)

    for i, (w, rect, _) in enumerate(old_cands):
        if i not in suppress_old:
            old_map[w.page_index].append(rect)

    for i, (w, rect, _) in enumerate(new_cands):
        if i not in suppress_new:
            new_map[w.page_index].append(rect)

    return (
        {k: merge_nearby_rects(dedupe_rects(v)) for k, v in old_map.items()},
        {k: merge_nearby_rects(dedupe_rects(v)) for k, v in new_map.items()},
    )
