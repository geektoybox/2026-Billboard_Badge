"""Tokenizer and independent style state for compact PocketReader markup."""

_STYLE_CHARS = 'bicurqs'
_STRUCTURAL = ('p', 'n', 'g', '*', 't', 'tr', 'td', '/t')
LAYOUT_VERSION = 4


def _features(value):
    result = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == 'h' and i + 1 < len(value) and value[i + 1] in '123456':
            result.append(value[i:i + 2])
            i += 2
        elif ch == 'f' and i + 1 < len(value) and value[i + 1] in '0123456789':
            end = i + 1
            while end < len(value) and value[end] in '0123456789':
                end += 1
            if end - i > 7:
                return None
            result.append(value[i:end])
            i = end
        elif ch in _STYLE_CHARS:
            result.append(ch)
            i += 1
        else:
            return None
    return result


def parse_style(tag):
    """Return (enable, disable), or None for an unrecognized tag."""
    if tag.count('/') > 1:
        return None
    parts = tag.split('/')
    enable = _features(parts[0])
    disable = _features(parts[1]) if len(parts) == 2 else []
    if enable is None or disable is None or not (enable or disable):
        return None
    return enable, disable


def tokenize(text):
    """Yield text, structural controls, or style updates, preserving unknown tags."""
    buffer = []
    i = 0
    while i < len(text):
        if text[i] != '<':
            next_tag = text.find('<', i)
            if next_tag < 0:
                buffer.append(text[i:])
                break
            buffer.append(text[i:next_tag])
            i = next_tag
            continue
        if i + 1 < len(text) and text[i + 1] == '<':
            buffer.append('<')
            i += 2
            continue
        end = text.find('>', i + 1)
        if end < 0:
            buffer.append(text[i:])
            break
        tag = text[i + 1:end]
        style = None if tag in _STRUCTURAL else parse_style(tag)
        if tag not in _STRUCTURAL and style is None:
            buffer.append(text[i:end + 1])
            i = end + 1
            continue
        if buffer:
            yield ('text', ''.join(buffer))
            buffer = []
        if tag in _STRUCTURAL:
            yield ('control', tag)
        else:
            yield ('style', style[0], style[1])
        i = end + 1
    if buffer:
        yield ('text', ''.join(buffer))


def apply_styles(state, enable, disable):
    """Mutate state; headings supersede, and unknown inactive closes are harmless."""
    for feature in enable:
        if feature.startswith('h'):
            state['heading'] = feature
        elif feature.startswith('f'):
            state['footnote'] = feature
        elif feature == 'c' or feature == 'r':
            state['alignment'] = feature
        else:
            state[feature] = True
    for feature in disable:
        if feature.startswith('h'):
            if state.get('heading') == feature:
                state['heading'] = None
        elif feature.startswith('f'):
            if state.get('footnote') == feature:
                state['footnote'] = None
        elif feature == 'c' or feature == 'r':
            if state.get('alignment') == feature:
                state['alignment'] = None
        else:
            state[feature] = False
    return state


def style_transition(previous, desired):
    """Encode a minimal state change using one combined compact tag."""
    enable, disable = [], []
    before = previous.get('heading')
    after = desired.get('heading')
    if before != after:
        if after:
            enable.append(after)
        elif before:
            disable.append(before)
    before, after = previous.get('footnote'), desired.get('footnote')
    if before != after:
        if after:
            enable.append(after)
        elif before:
            disable.append(before)
    before, after = previous.get('alignment'), desired.get('alignment')
    if before != after:
        if after:
            enable.append(after)
        elif before:
            disable.append(before)
    for feature in ('b', 'i', 'u', 'q', 's'):
        before, after = bool(previous.get(feature)), bool(desired.get(feature))
        if before != after:
            (enable if after else disable).append(feature)
    if not enable and not disable:
        return ''
    return '<' + ''.join(enable) + ('/' + ''.join(disable) if disable else '') + '>'


def logical_blocks(text):
    """Parse compact text into logical blocks for a future shared layout engine.

    This preserves independent inline styles and structural controls. It is
    deliberately separate from the current badge pagination until the new
    layout and sidecar index format are implemented together.
    """
    state = {}
    runs = []
    table = row = cell = False
    for token in tokenize(text):
        if token[0] == 'style':
            # Headings are block-level: a new heading (or closing the active
            # heading) starts a new record while other styles remain active.
            heading_before = state.get('heading')
            changes_heading = any(feature.startswith('h') for feature in token[1]) or heading_before in token[2]
            if changes_heading and runs:
                yield ('runs', runs)
                runs = []
            apply_styles(state, token[1], token[2])
        elif token[0] == 'text':
            if token[1]:
                runs.append((token[1], dict(state)))
        else:
            control = token[1]
            if runs:
                yield ('runs', runs)
                runs = []
            if control == 't':
                table, row, cell = True, False, False
                yield ('table_start',)
            elif control == 'tr' and table:
                if cell:
                    yield ('cell_end',)
                    cell = False
                if row:
                    yield ('row_end',)
                row = True
                yield ('row_start',)
            elif control == 'td' and table:
                if cell:
                    yield ('cell_end',)
                cell = True
                yield ('cell_start',)
            elif control == '/t' and table:
                if cell:
                    yield ('cell_end',)
                if row:
                    yield ('row_end',)
                yield ('table_end',)
                table = row = cell = False
            else:
                yield ('control', control)
    if runs:
        yield ('runs', runs)


def wrap_runs(runs, advances, width, advances_for_style=None, indent=0, with_positions=False):
    """Wrap inline styled text while keeping style changes inside words.

    Each returned line is a list of (text, style) runs. Advance widths are
    the same table used by the badge, so a caller can use these lines for
    both page indexing and drawing.
    """
    words = []
    word = []
    for text, style in runs:
        for char in text:
            if char.isspace():
                if word:
                    words.append(word)
                    word = []
            elif word and word[-1][1] == style:
                word[-1] = (word[-1][0] + char, word[-1][1])
            else:
                word.append((char, style))
    if word:
        words.append(word)

    def advance(char, style):
        small = style.get('s') and 'a' <= char <= 'z'
        if small:
            char = char.upper()
        code = ord(char)
        selected = advances_for_style(style) if advances_for_style else advances
        value = selected[code - 32] if 32 <= code <= 126 else selected[95 + "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠".find(char)] if char in "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠" and 95 + "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠".find(char) < len(selected) else selected[31]
        return max(1, (value * 3 + 3) // 4) if small else value

    lines, line, line_width, has_word, first_word = [], [], 0, False, 0
    if words and indent:
        line = [(' ' * indent, words[0][0][1])]
        line_width = advance(' ', words[0][0][1]) * indent
    for word_index, pieces in enumerate(words):
        space_width = advance(' ', pieces[0][1])
        remaining = sum(advance(char, style) for piece, style in pieces for char in piece)
        if has_word and line_width + space_width + remaining > width:
            lines.append((line, first_word) if with_positions else line)
            line, line_width, has_word = [], 0, False
        if has_word:
            line.append((' ', pieces[0][1]))
            line_width += space_width
        for piece, style in pieces:
            for char in piece:
                char_width = advance(char, style)
                if has_word and line_width + char_width > width:
                    lines.append((line, first_word) if with_positions else line)
                    line, line_width, has_word = [], 0, False
                if not has_word:
                    first_word = word_index
                if line and line[-1][1] == style:
                    line[-1] = (line[-1][0] + char, style)
                else:
                    line.append((char, style))
                line_width += char_width
                has_word = True
    if line:
        lines.append((line, first_word) if with_positions else line)
    return lines


def encode_runs(runs):
    """Encode styled runs as a self-contained record with escaped text.

    Each record begins from default state, allowing a future index to seek to
    a paragraph without scanning the entire book to reconstruct earlier styles.
    """
    result = []
    previous = {}
    for text, style in runs:
        result.append(style_transition(previous, style))
        result.append(text.replace('<', '<<'))
        previous = dict(style)
    return ''.join(result)


def decode_runs(record):
    """Decode one self-contained record into styled text runs."""
    state, runs = {}, []
    for token in tokenize(record):
        if token[0] == 'style':
            apply_styles(state, token[1], token[2])
        elif token[0] == 'text' and token[1]:
            runs.append((token[1], dict(state)))
    return runs


def layout_table(rows, advances, width, with_positions=False, advances_for_style=None):
    """Lay out a small table in columns when possible, otherwise stack cells.

    Each row is a list of cells, each cell a list of styled text runs. The
    returned value is a list of styled lines. It never clips cell contents.
    """
    if not rows:
        return []
    count = max([len(row) for row in rows])
    if not count:
        return []
    space = max(1, advances[0])

    def measure(runs):
        total = 0
        for text, style in runs:
            for char in text:
                small = style.get('s') and 'a' <= char <= 'z'
                if small:
                    char = char.upper()
                code = ord(char)
                selected = advances_for_style(style) if advances_for_style else advances
                value = selected[code - 32] if 32 <= code <= 126 else selected[95 + "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠".find(char)] if char in "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠" and 95 + "¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿāăēĕėīĭōŏŒœūŭſǽȳ˘̸ΑΓΔΕΛΜΝΞΠάέήίαβγδεζηθικλμνξοπρςστυφχψωόύώἀἁἄἅἐἔἠἡἦἰἱἷἸὀὁὄὐὑὔὡὦὰὲὴὶὸὺὼῆῇῖῥῦῳῶῷ‐–—‘’‛“”†‡•…′″⁄⅓⅛✠".find(char) < len(selected) else selected[31]
                total += max(1, (value * 3 + 3) // 4) if small else value
        return total

    widths = [0] * count
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], measure(cell))
    widths = [((value + space - 1) // space) * space for value in widths]
    if sum(widths) + max(0, count - 1) * 2 * space > width:
        result = []
        word_offset = 0
        for row in rows:
            for cell in row:
                if with_positions:
                    for line, word in wrap_runs(cell, advances, width, advances_for_style, with_positions=True):
                        result.append((line, word_offset + word))
                    word_offset += len(''.join(text for text, _ in cell).split())
                else:
                    result.extend(wrap_runs(cell, advances, width, advances_for_style))
        return result
    result = []
    word_offset = 0
    for row in rows:
        line = []
        for index in range(count):
            cell = row[index] if index < len(row) else []
            line.extend(cell)
            if index + 1 < count:
                pad = (widths[index] - measure(cell) + space - 1) // space + 2
                line.append((' ' * pad, {}))
        result.append((line, word_offset) if with_positions else line)
        if with_positions:
            word_offset += sum(len(''.join(text for text, _ in cell).split()) for cell in row)
    return result


def layout_record(record, advances, width, advances_for_style=None, indent=0):
    """Wrap a single seekable text record using the shared glyph advances."""
    return wrap_runs(decode_runs(record), advances, width, advances_for_style, indent)


def _read_table(handle, body_end):
    """Read a bounded table so it can be measured before rendering."""
    rows, row, cell = [], None, None
    total, cells = 0, 0
    while True:
        if body_end is not None and handle.tell() >= body_end:
            raise ValueError('Incomplete table')
        raw = handle.readline()
        if not raw:
            raise ValueError('Incomplete table')
        total += len(raw)
        if total > 131072:
            raise ValueError('Table is too large for this reader')
        record = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if record == '</t>':
            return rows
        if record == '<tr>':
            row, cell = [], None
            rows.append(row)
        elif record == '<td>':
            if row is None:
                row = []
                rows.append(row)
            cell = []
            row.append(cell)
            cells += 1
            if cells > 256:
                raise ValueError('Table has too many cells for this reader')
        elif record in ('<n>', '<p>'):
            if cell:
                cell.append((' ', {}))
        elif cell is not None:
            if cell:
                cell.append((' ', {}))
            cell.extend(decode_runs(record))


def iter_layout_records(handle, start, advances, width, advances_for_style=None, with_positions=False, body_end=None):
    """Stream seekable records as (byte offset, line index, styled runs).

    Page breaks use None as the line. Scene dividers yield a centered line
    with space above and below; the page builder decides which boundary
    spaces to omit. Table cells are currently emitted in reading order.
    """
    handle.seek(start)
    word_offset = 0
    while True:
        offset = handle.tell()
        if body_end is not None and offset >= body_end:
            return
        raw = handle.readline()
        if not raw:
            return
        record = raw.decode('utf-8', 'replace').rstrip('\r\n')
        if record == '<g>':
            yield (offset, 0, word_offset, None) if with_positions else (offset, 0, None)
        elif record == '<*>':
            for index, value in enumerate(([], [('* * *', {'alignment': 'c'})], [])):
                yield (offset, index, word_offset, value) if with_positions else (offset, index, value)
        elif record == '<t>':
            rows = _read_table(handle, body_end)
            lines = layout_table(rows, advances, width, with_positions, advances_for_style)
            for line_index, line in enumerate(lines):
                if with_positions:
                    runs, first_word = line
                    yield offset, line_index, word_offset + first_word, runs
                else:
                    yield offset, line_index, line
            word_offset += sum(len(''.join(text for text, _ in cell).split())
                               for row in rows for cell in row)
        elif record in ('<p>', '<n>', '<tr>', '<td>', '</t>'):
            continue
        else:
            runs = decode_runs(record)
            style = runs[0][1] if runs else {}
            indent = 4 if record.startswith('<p>') and not style.get('heading') and not style.get('alignment') else 0
            word_count = len(''.join(text for text, _ in runs).split())
            available = max(1, width - 32) if style.get('q') else width
            wrapped = wrap_runs(runs, advances, available, advances_for_style, indent, with_positions)
            for line_index, line in enumerate(wrapped):
                if with_positions:
                    line_runs, first_word = line
                    yield offset, line_index, word_offset + first_word, line_runs
                else:
                    yield offset, line_index, line
            word_offset += word_count


def iter_compact_pages(handle, start, advances, width, line_limit, advances_for_style=None, with_positions=False, row_span_for_style=None, skip_initial_lines=0, body_end=None):
    """Build prototype pages from the same styled lines used for drawing."""
    if line_limit <= 0:
        raise ValueError('Page must have room for at least one line')
    page = []
    blank = None
    for item in iter_layout_records(handle, start, advances, width, advances_for_style, with_positions, body_end):
        if item[0] == start and item[1] < skip_initial_lines:
            continue
        runs = item[-1]
        if runs is None:
            if page:
                yield page
            page, blank = [], None
            continue
        if not runs:
            blank = item
            continue
        span = max([row_span_for_style(style) for _, style in runs]) if row_span_for_style else 1
        span = max(1, min(line_limit, span))
        # A divider may close the page by itself; don't sacrifice that last
        # slot to its preceding blank line. Leading and trailing blank lines
        # are never carried over to a new page.
        if blank and page and len(page) + 1 + span <= line_limit:
            page.append(blank)
        blank = None
        if page and len(page) + span > line_limit:
            yield page
            page = []
        page.append(item)
        for _ in range(span - 1):
            page.append(item[:-1] + ([('', {'_reserved': True})],))
        if len(page) >= line_limit:
            yield page
            page = []
    if page:
        yield page


def paginate_with_footnotes(lines, line_limit):
    """Prototype footnote placement from (body line, referenced note lines).

    The source page reserves at most half its rows for notes and the
    continuation notice. Overflow occupies footnote-only pages immediately
    after that page. This helper does not yet encode note references in a
    book or persist their page offsets.
    """
    if line_limit < 2:
        raise ValueError('A page needs at least two lines')
    cap = line_limit // 2
    body, notes, noted, blank = [], [], set(), None

    def finish():
        if not body:
            return []
        has_overflow = len(notes) > cap
        visible = notes[:cap - 1] if has_overflow else notes[:cap]
        rest = notes[len(visible):]
        pages = [{'body': list(body), 'footnotes': visible,
                  'notice': has_overflow, 'continuation': False}]
        while rest:
            has_more = len(rest) > line_limit
            count = line_limit - 1 if has_more else line_limit
            pages.append({'body': [], 'footnotes': rest[:count],
                          'notice': has_more, 'continuation': True})
            rest = rest[count:]
        return pages

    for line, referenced_notes in lines:
        if line is None:
            for page in finish():
                yield page
            body, notes, noted, blank = [], [], set(), None
            continue
        if isinstance(line, dict) and '_blank' in line:
            blank = line['_blank']
            continue
        def collect(existing):
            incoming, added = [], set()
            for note in referenced_notes:
                ident = note[0][0] if note and isinstance(note[0], tuple) else None
                if ident and (ident in existing or ident in added):
                    continue
                incoming.extend(note)
                if ident:
                    added.add(ident)
            return incoming, added
        incoming, added = collect(noted)
        reserve = min(cap, len(notes) + len(incoming))
        rows = line['_rows'] if isinstance(line, dict) and '_rows' in line else [line]
        if body and len(body) + len(rows) + reserve > line_limit:
            for page in finish():
                yield page
            body, notes, noted, blank = [], [], set(), None
            incoming, added = collect(noted)
        if blank is not None and body and len(body) + 1 + len(rows) + reserve <= line_limit:
            body.append(blank)
        blank = None
        body.extend(rows)
        notes.extend(incoming)
        noted.update(added)
    for page in finish():
        yield page


def iter_footnoted_preview(handle, start, advances, width, line_limit, notes,
                           advances_for_style=None, row_span_for_style=None, body_end=None):
    """Prototype paginated note placement from tagged references and note text.

    This is not yet the device index format. Each body element retains its
    source byte offset and line number for eventual seekable page indexing.
    """
    def source_lines():
        for offset, line_index, word, runs in iter_layout_records(
                handle, start, advances, width, advances_for_style, with_positions=True, body_end=body_end):
            if runs is None:
                yield None, []
                continue
            if not runs:
                yield {'_blank': (offset, line_index, word, runs)}, []
                continue
            references = []
            seen = set()
            for _, style in runs:
                ident = style.get('footnote')
                if ident and ident not in seen and ident in notes:
                    seen.add(ident)
                    wrapped = wrap_runs([(notes[ident], {})], advances, width)
                    references.append([(ident, number, line) for number, line in enumerate(wrapped)])
            item = (offset, line_index, word, runs)
            span = max([row_span_for_style(style) for _, style in runs]) if row_span_for_style else 1
            span = max(1, min(line_limit, span))
            rows = [item] + [(offset, line_index, word, [('', {'_reserved': True})]) for _ in range(span - 1)]
            yield {'_rows': rows}, references
    for page in paginate_with_footnotes(source_lines(), line_limit):
        yield page


def footnote_page_descriptor(page, base_offset=0):
    """Return compact seek data for a page that contains footnote content."""
    if not (page['footnotes'] or page['notice'] or page['continuation']):
        return None
    return {
        'body': [[row[0] - base_offset, row[1], bool(row[3] and row[3][0][1].get('_reserved'))]
                 for row in page['body']],
        'notes': [[note[0], note[1]] for note in page['footnotes']],
        'notice': bool(page['notice']), 'continuation': bool(page['continuation'])}
