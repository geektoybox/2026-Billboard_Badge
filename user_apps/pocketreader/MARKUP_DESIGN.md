# Compact book markup: working design

This document describes the compact markup used by schema 5 books. The reader
continues to support the earlier book schemas. Some details of conversion and
layout remain subject to device testing.

Styles are independent state. A new heading level replaces the previous one;
other styles persist. In a combined tag, features before `/` turn on and
features after `/` turn off. Turning off an inactive feature has no effect.
For example, `<h1bc>Title<h2>Author<i/h2b>text</i>` leaves centering active
until `</c>`. Heading alignment follows the source, not a centered default.
Styles include headings, bold (`b`), italic (`i`), underline (`u`), small caps
(`s`), center (`c`), right alignment (`r`), and block quotes (`q`).

`<p>` is a paragraph break; normal new paragraphs have a first-line indent.
`<n>` is a line break without starting an indented paragraph. `<g>` is a forced
page break. `<*>` is a centered scene divider with a blank line before and
after; if the divider fits at the bottom of a page, omit the trailing blank
line and start the next scene on the next page. If the divider cannot fit,
move it to the next page. A literal opening bracket is encoded as `<<`.

`<t>` begins a table, `<tr>` a row, and `<td>` a cell. A new row closes the
previous row and cell; a new cell closes the previous cell. `</t>` closes the
cell, row, and table. Tables are displayed in columns when they fit; otherwise
their cells are displayed in reading order. Oversized tables are split at row
boundaries during conversion.

Footnotes appear at the bottom of the page containing their reference and
together occupy no more than half the reading area. Overflow continues on
one or more footnote-only pages, then normal reading resumes. Footnote IDs use
`f` followed by a number, and the note text is stored separately after the body.
The current reader limits the combined footnote data to 256 KiB.

The converter, reader, and indexer share layout rules for each font and size.
Existing book formats remain readable; changed font metrics may require an
index for that font and size to be rebuilt.
