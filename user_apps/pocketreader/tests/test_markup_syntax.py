import importlib.util
import io
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'overlay/lib/pocketreader_markup.py'
spec = importlib.util.spec_from_file_location('pocketreader_markup', PATH)
markup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(markup)


class MarkupSyntaxTests(unittest.TestCase):
    def test_independent_features_and_superseded_headings(self):
        state = {}
        observed = []
        for token in markup.tokenize('<h1bc>Title<h2>Author<i/h2b>text</i>'):
            if token[0] == 'style':
                markup.apply_styles(state, token[1], token[2])
            else:
                observed.append((token[1], dict(state)))
        self.assertEqual(observed[0][1], {'heading': 'h1', 'b': True, 'alignment': 'c'})
        self.assertEqual(observed[1][1], {'heading': 'h2', 'b': True, 'alignment': 'c'})
        self.assertEqual(observed[2][1], {'heading': None, 'b': False, 'alignment': 'c', 'i': True})
        self.assertFalse(state['i'])

    def test_literal_brackets_and_unknown_tags_survive(self):
        tokens = list(markup.tokenize('x <<b> y <unknown> z'))
        self.assertEqual(tokens, [('text', 'x <b> y <unknown> z')])

    def test_table_and_break_controls(self):
        controls = [token[1] for token in markup.tokenize('<t><tr><td>A<td>B</t><p><n><g><*>') if token[0] == 'control']
        self.assertEqual(controls, ['t', 'tr', 'td', 'td', '/t', 'p', 'n', 'g', '*'])

    def test_closing_inactive_and_superseded_heading_is_safe(self):
        state = {'heading': 'h2', 'alignment': 'c'}
        markup.apply_styles(state, [], ['h1', 'b', 'r'])
        self.assertEqual(state['heading'], 'h2')
        self.assertEqual(state['alignment'], 'c')
        self.assertFalse(state['b'])

    def test_wrap_preserves_inline_style_within_word(self):
        regular, italic = {}, {'i': True}
        lines = markup.wrap_runs([('one exp', regular), ('lor', italic), ('e two', regular)], [1] * 95, 8)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['one', 'explore', 'two'])
        self.assertEqual(lines[1], [('exp', regular), ('lor', italic), ('e', regular)])

    def test_transition_uses_a_single_combined_tag(self):
        before = {'heading': 'h2', 'b': True, 'alignment': 'c'}
        after = {'heading': None, 'b': False, 'i': True, 'alignment': 'c'}
        self.assertEqual(markup.style_transition(before, after), '<i/h2b>')
        state = dict(before)
        token = list(markup.tokenize(markup.style_transition(before, after)))[0]
        markup.apply_styles(state, token[1], token[2])
        for key in after:
            self.assertEqual(bool(state.get(key)), bool(after[key]))
        self.assertEqual(state['alignment'], 'c')

    def test_underline_is_an_independent_style(self):
        state = {}
        tokens = list(markup.tokenize('<bu>one<i/b>two</iu>'))
        self.assertEqual(tokens[0], ('style', ['b', 'u'], []))
        markup.apply_styles(state, tokens[0][1], tokens[0][2])
        self.assertTrue(state['u'])
        markup.apply_styles(state, tokens[2][1], tokens[2][2])
        self.assertTrue(state['u'])
        self.assertFalse(state['b'])
        markup.apply_styles(state, tokens[4][1], tokens[4][2])
        self.assertFalse(state['u'])

    def test_small_caps_wrap_using_reduced_uppercase_width(self):
        advances = [8] * 95
        advances[ord('A') - 32] = 12
        advances[ord('a') - 32] = 4
        runs = markup.decode_runs('<s>Aaa</s>')
        self.assertTrue(runs[0][1]['s'])
        self.assertEqual(len(markup.wrap_runs(runs, advances, 30)), 1)
        self.assertEqual(len(markup.wrap_runs(runs, advances, 29)), 2)

    def test_footnote_reference_is_an_inline_style(self):
        runs = [('Text ', {}), ('[1]', {'footnote': 'f1'}), (' continues', {})]
        encoded = markup.encode_runs(runs)
        self.assertEqual(encoded, 'Text <f1>[1]</f1> continues')
        decoded = markup.decode_runs(encoded)
        self.assertEqual(''.join(text for text, _ in decoded), 'Text [1] continues')
        self.assertEqual(decoded[1][1]['footnote'], 'f1')
        self.assertIsNone(decoded[2][1]['footnote'])

    def test_table_implicit_row_end(self):
        blocks = list(markup.logical_blocks('<t><tr><td>A<td>B<tr><td>C</t>'))
        self.assertEqual([block[0] for block in blocks], ['table_start', 'row_start', 'cell_start',
                         'runs', 'cell_end', 'cell_start', 'runs', 'cell_end', 'row_end',
                         'row_start', 'cell_start', 'runs', 'cell_end', 'row_end', 'table_end'])

    def test_small_tables_keep_columns_and_wide_tables_keep_all_content(self):
        rows = [[[('Name', {})], [('Age', {})]], [[('Al', {})], [('42', {})]]]
        lines = markup.layout_table(rows, [1] * 95, 20)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['Name  Age', 'Al    42'])
        lines = markup.layout_table(rows, [1] * 95, 5)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['Name', 'Age', 'Al', '42'])

    def test_streamed_table_can_be_sought_from_its_start(self):
        body = b'<t>\n<tr>\n<td>\nName\n<td>\nAge\n<tr>\n<td>\nAl\n<td>\n42\n</t>\nAfter\n'
        lines = list(markup.iter_layout_records(io.BytesIO(body), 0, [1] * 95, 20, with_positions=True))
        self.assertEqual([''.join(text for text, _ in line[3]) for line in lines], ['Name  Age', 'Al    42', 'After'])
        self.assertEqual([line[2] for line in lines], [0, 2, 4])
        self.assertEqual(lines[0][0], lines[1][0])

    def test_wide_table_split_word_keeps_one_word_position(self):
        body = b'<t>\n<tr>\n<td>\nextraordinary\n<td>\ntail\n</t>\nAfter\n'
        lines = list(markup.iter_layout_records(io.BytesIO(body), 0, [1] * 95, 5, with_positions=True))
        self.assertEqual([line[2] for line in lines], [0, 0, 0, 1, 2])

    def test_footnote_overflow_gets_its_own_pages(self):
        source = [('Text one', []), ('Text two', [['n1', 'n2', 'n3', 'n4', 'n5', 'n6']]),
                  ('Text three', []), ('Text four', [])]
        pages = list(markup.paginate_with_footnotes(source, 6))
        self.assertEqual(pages[0]['body'], ['Text one', 'Text two', 'Text three'])
        self.assertEqual(pages[0]['footnotes'], ['n1', 'n2'])
        self.assertTrue(pages[0]['notice'])
        self.assertEqual(pages[1]['body'], [])
        self.assertEqual(pages[1]['footnotes'], ['n3', 'n4', 'n5', 'n6'])
        self.assertTrue(pages[1]['continuation'])
        self.assertEqual(pages[2]['body'], ['Text four'])

    def test_referenced_notes_make_room_on_source_page(self):
        source = [('A', []), ('B', []), ('C', [['note one', 'note two']]), ('D', [])]
        pages = list(markup.paginate_with_footnotes(source, 4))
        self.assertEqual(pages[0]['body'], ['A', 'B'])
        self.assertEqual(pages[1]['body'], ['C', 'D'])
        self.assertEqual(pages[1]['footnotes'], ['note one', 'note two'])

    def test_tagged_reference_places_note_on_source_page(self):
        stream = io.BytesIO(b'Hello <f1>[1]</f1>\nAfter\n')
        pages = list(markup.iter_footnoted_preview(stream, 0, [1] * 95, 12, 6,
                                                {'f1': '[1] note text'}))
        self.assertEqual(len(pages), 1)
        self.assertEqual([''.join(text for text, _ in item[-1]) for item in pages[0]['body']],
                         ['Hello [1]', 'After'])
        self.assertEqual([''.join(text for text, _ in line[2]) for line in pages[0]['footnotes']],
                         ['[1] note', 'text'])

    def test_repeated_reference_does_not_duplicate_same_page_note(self):
        stream = io.BytesIO(b'<f1>[1]</f1> text\n<f1>[1]</f1> again\n')
        pages = list(markup.iter_footnoted_preview(stream, 0, [1] * 95, 20, 6, {'f1': '[1] note'}))
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]['footnotes']), 1)

    def test_footnote_layout_preserves_scene_break_page_rule(self):
        stream = io.BytesIO(b'One\nTwo\n<*>\nNext\n')
        pages = list(markup.iter_footnoted_preview(stream, 0, [1] * 95, 20, 3, {}))
        visible = [[''.join(text for text, _ in item[-1]) for item in page['body']] for page in pages]
        self.assertEqual(visible, [['One', 'Two', '* * *'], ['Next']])

    def test_self_contained_runs_round_trip(self):
        runs = [('A < B', {'heading': 'h1', 'b': True, 'alignment': 'c'}),
                ('Author', {'heading': 'h2', 'b': True, 'alignment': 'c'}),
                ('End', {'i': True, 'alignment': 'c'})]
        encoded = markup.encode_runs(runs)
        self.assertIn('<<', encoded)
        state, decoded = {}, []
        for token in markup.tokenize(encoded):
            if token[0] == 'style': markup.apply_styles(state, token[1], token[2])
            else: decoded.append((token[1], dict(state)))
        self.assertEqual([item[0] for item in decoded], [item[0] for item in runs])
        for (_, actual), (_, expected) in zip(decoded, runs):
            for feature in ('heading', 'alignment', 'b', 'i', 'q'):
                self.assertEqual(actual.get(feature) or None, expected.get(feature) or None)

    def test_heading_changes_create_separate_blocks_but_keep_other_styles(self):
        blocks = list(markup.logical_blocks('<h1bc>Title<h2>Author<i/h2b>End</i>'))
        self.assertEqual([item[1][0][0] for item in blocks], ['Title', 'Author', 'End'])
        self.assertEqual(blocks[1][1][0][1]['heading'], 'h2')
        self.assertTrue(blocks[1][1][0][1]['b'])
        self.assertEqual(blocks[2][1][0][1]['alignment'], 'c')

    def test_seekable_record_preserves_styles_when_wrapped(self):
        lines = markup.layout_record('<b>one <i>two</b> three', [1] * 95, 7)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['one two', 'three'])
        self.assertTrue(lines[0][0][1]['b'])
        self.assertTrue(lines[0][-1][1]['i'])
        self.assertFalse(lines[1][0][1]['b'])

    def test_heading_uses_its_own_font_metrics(self):
        ordinary, heading = [1] * 95, [2] * 95
        select = lambda style: heading if style.get('heading') else ordinary
        lines = markup.layout_record('<h1>abc def', ordinary, 7, select)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['abc', 'def'])

    def test_first_line_indent_counts_toward_wrapping(self):
        lines = markup.layout_record('abc def ghi', [1] * 95, 8, indent=2)
        self.assertEqual([''.join(text for text, _ in line) for line in lines], ['  abc', 'def ghi'])

    def test_wrapped_fragments_keep_original_word_position(self):
        lines = markup.wrap_runs([('one extraordinary tail', {})], [1] * 95, 5, with_positions=True)
        self.assertEqual([word for _, word in lines], [0, 1, 1, 1, 2])

    def test_stream_keeps_byte_positions_and_scene_break(self):
        stream = io.BytesIO(b'<p>\n<bc>Alpha beta\n<*>\n<g>\n')
        items = list(markup.iter_layout_records(stream, 0, [1] * 95, 8))
        self.assertEqual([item[0] for item in items], [4, 4, 19, 19, 19, 23])
        self.assertEqual(''.join(piece for piece, _ in items[2][2]), '')
        self.assertEqual(items[3][2], [('* * *', {'alignment': 'c'})])
        self.assertIsNone(items[-1][2])

    def test_seekable_paragraph_record_includes_its_indent(self):
        stream = io.BytesIO(b'<p>First line\n<c>Title\n')
        lines = list(markup.iter_layout_records(stream, 0, [1] * 95, 20))
        self.assertEqual(''.join(text for text, _ in lines[0][2]), '    First line')
        self.assertEqual(''.join(text for text, _ in lines[1][2]), 'Title')

    def test_stream_positions_do_not_count_tags_or_split_words(self):
        stream = io.BytesIO(b'<p>one extraordinary\n<*>\n<b>tail end\n')
        lines = list(markup.iter_layout_records(stream, 0, [1] * 95, 5, with_positions=True))
        self.assertEqual([line[2] for line in lines], [0, 0, 1, 1, 1, 2, 2, 2, 2, 3])

    def test_scene_divider_can_close_page_without_blank_padding(self):
        stream = io.BytesIO(b'One\nTwo\n<*>\nNext\n')
        pages = list(markup.iter_compact_pages(stream, 0, [1] * 95, 20, 3))
        visible = [[''.join(text for text, _ in line[2]) for line in page] for page in pages]
        self.assertEqual(visible, [['One', 'Two', '* * *'], ['Next']])

    def test_scene_divider_keeps_space_when_it_fits(self):
        stream = io.BytesIO(b'One\n<*>\nNext\n')
        pages = list(markup.iter_compact_pages(stream, 0, [1] * 95, 20, 3))
        visible = [[''.join(text for text, _ in line[2]) for line in page] for page in pages]
        self.assertEqual(visible, [['One', '', '* * *'], ['Next']])

    def test_large_heading_moves_if_its_height_would_not_fit(self):
        stream = io.BytesIO(b'One\nTwo\n<h1>Heading\n')
        span = lambda style: 2 if style.get('heading') else 1
        pages = list(markup.iter_compact_pages(stream, 0, [1] * 95, 20, 3, row_span_for_style=span))
        self.assertEqual([len(page) for page in pages], [2, 2])
        self.assertEqual(pages[1][0][-1][0][0], 'Heading')
        self.assertTrue(pages[1][1][-1][0][1]['_reserved'])

    def test_page_positions_can_recover_styled_line(self):
        body = b'<p><b>one extraordinary tail\n<*>\n<p><ci>second passage\n'
        stream = io.BytesIO(body)
        pages = list(markup.iter_compact_pages(stream, 0, [1] * 95, 5, 3, with_positions=True))
        for page in pages:
            offset, line_number, _, expected = page[0]
            resumed = list(markup.iter_layout_records(io.BytesIO(body), offset, [1] * 95, 5))
            actual = next(line for pos, number, line in resumed if pos == offset and number == line_number)
            self.assertEqual(actual, expected)

    def test_page_is_reconstructed_directly_from_its_position(self):
        body = b'<p><b>one extraordinary tail\n<*>\n<p><ci>second passage\n'
        pages = list(markup.iter_compact_pages(io.BytesIO(body), 0, [1] * 95, 5, 3))
        for page in pages:
            offset, line_number, _ = page[0]
            resumed = next(markup.iter_compact_pages(io.BytesIO(body), offset, [1] * 95, 5, 3,
                                                    skip_initial_lines=line_number))
            self.assertEqual(resumed, page)

    def test_quote_block_wraps_with_space_for_indents(self):
        stream = io.BytesIO(b'<q>abcdefghij abcdefghij\n')
        lines = list(markup.iter_layout_records(stream, 0, [1] * 95, 42))
        self.assertEqual([''.join(text for text, _ in line[2]) for line in lines], ['abcdefghij', 'abcdefghij'])


if __name__ == '__main__':
    unittest.main()
