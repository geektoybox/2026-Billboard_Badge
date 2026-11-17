#!/usr/bin/env python3
"""Validate a PocketReader book and its optional index using Python only."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path


def decode_pages(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError('Invalid page-index encoding') from exc
    if len(data) > 1200000:
        raise ValueError('Page index is too large')
    values, pages = [], []
    value = shift = offset = word = 0
    for byte in data:
        value |= (byte & 127) << shift
        if byte & 128:
            shift += 7
            if shift > 35: raise ValueError('Page-index value is too large')
            continue
        values.append(value); value = shift = 0
        if len(values) == 3:
            offset += values[0]; word += values[2]
            pages.append((offset, values[1], word)); values = []
            if len(pages) > 100000: raise ValueError('Too many pages')
    if shift or values or not pages: raise ValueError('Incomplete page index')
    return pages


def validate_book(book_path, index_path=None):
    book_path=Path(book_path);raw=book_path.read_bytes()
    head, sep, body=raw.partition(b'\n')
    if not sep or len(head)>65536: raise ValueError('Invalid book header')
    header=json.loads(head)
    if not isinstance(header,dict) or header.get('schema') not in (2,3,4,5):
        raise ValueError('Expected a reflowable schema 2, 3, 4, or 5 book')
    book_id=header.get('id'); total=header.get('word_count')
    if (not isinstance(book_id,str) or not 1 <= len(book_id) <= 96
            or any(not ('a' <= c <= 'z' or 'A' <= c <= 'Z' or '0' <= c <= '9' or c in '-_') for c in book_id)
            or type(total) is not int or total<1):
        raise ValueError('Invalid book ID or word count')
    notes = {}
    if 'body_length' in header:
        length=header['body_length']
        if type(length) is not int or not 0 < length <= len(body): raise ValueError('Invalid body length')
        if header['schema'] == 5:
            note_length = header.get('notes_length', 0)
            if type(note_length) is not int or note_length < 0 or note_length > 262144 or length + note_length > len(body):
                raise ValueError('Invalid footnote section length')
            if note_length:
                notes = json.loads(body[length:length + note_length])
                if not isinstance(notes, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in notes.items()):
                    raise ValueError('Invalid footnote section')
        body=body[:length]
    words=0
    markup = None
    if header['schema'] == 5:
        spec = importlib.util.spec_from_file_location('pocketreader_markup',
            Path(__file__).resolve().parent / 'overlay/lib/pocketreader_markup.py')
        markup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(markup)
    for record in body.decode('utf-8').split('\n'):
        record=record.rstrip('\r')
        if markup is not None:
            runs = markup.decode_runs(record)
            if any(style.get('footnote') and style['footnote'] not in notes for _, style in runs):
                raise ValueError('Footnote reference is missing its note')
            words += len(''.join(text for text, _ in runs).split())
            continue
        if record=='\x1e': continue
        if record.startswith('\x1f'):
            if header['schema'] != 4 or len(record)<3 or record[1] not in 'CHL':
                raise ValueError('Unknown formatting marker')
            record=record[2:]
        if '\x1e' in record or '\x1f' in record: raise ValueError('Formatting marker inside a text record')
        words+=len(record.split())
    if words != total: raise ValueError('Book word count does not match its content')
    size_count=chapter_count=0
    if index_path is not None:
        index=json.loads(Path(index_path).read_text(encoding='utf-8'))
        if (not isinstance(index,dict) or index.get('version') != 1 or index.get('book_id') != book_id
                or index.get('sha256') != hashlib.sha256(raw).hexdigest()):
            raise ValueError('Index does not match this book')
        chapters=index.get('chapters',[]);sizes=index.get('sizes',{})
        if not isinstance(chapters,list) or not isinstance(sizes,dict): raise ValueError('Invalid index data')
        previous=-1
        for chapter in chapters:
            if (not isinstance(chapter,list) or len(chapter)!=2 or type(chapter[0]) is not int
                    or not previous<=chapter[0]<total or not isinstance(chapter[1],str) or not chapter[1]):
                raise ValueError('Invalid chapter position')
            previous=chapter[0]
        for key, value in sizes.items():
            if not isinstance(key,str) or not isinstance(value,dict): raise ValueError('Invalid size index')
            if value.get('encoding') != 'delta-varint-base64': raise ValueError('Unsupported page-index encoding')
            if value.get('layout_version') != (4 if header['schema']==5 else 2 if header['schema']==4 else 1): raise ValueError('Wrong layout version')
            if header['schema']==4 and len(value.get('heading_metrics_sha256','')) != 64:
                raise ValueError('Missing heading font metrics')
            if header['schema']==5 and len(value.get('style_metrics_sha256','')) != 64:
                raise ValueError('Missing styled font metrics')
            pages = decode_pages(value.get('pages',''))
            if header['schema']==5 and notes:
                note_pages = value.get('note_pages')
                if not isinstance(note_pages, dict) or not note_pages:
                    raise ValueError('Missing footnote page information')
                for page_number, detail in note_pages.items():
                    if not isinstance(page_number, str) or not page_number.isdigit() or int(page_number) >= len(pages) or not isinstance(detail, dict):
                        raise ValueError('Invalid footnote page')
                    rows, footer = detail.get('body'), detail.get('notes')
                    if not isinstance(rows, list) or not isinstance(footer, list) or len(rows) + len(footer) + bool(detail.get('notice')) > value.get('lines', 0):
                        raise ValueError('Footnote page does not fit')
                    if detail.get('continuation') and rows:
                        raise ValueError('Footnote continuation contains body text')
                    if not detail.get('continuation') and len(footer) + bool(detail.get('notice')) > value.get('lines', 0) // 2:
                        raise ValueError('Footnotes occupy more than half the source page')
                    for row in rows:
                        if not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int or not 0 <= row[0] < len(body) or type(row[1]) is not int or row[1] < 0 or type(row[2]) is not bool:
                            raise ValueError('Invalid footnote body position')
                    for note in footer:
                        if not isinstance(note, list) or len(note) != 2 or note[0] not in notes or type(note[1]) is not int or note[1] < 0:
                            raise ValueError('Invalid footnote position')
            for offset, line, word in pages:
                if offset>=len(body) or word>total: raise ValueError('Page position outside book')
            size_count+=1
        chapter_count=len(chapters)
    return {'title':header.get('title',book_id),'schema':header['schema'],
            'word_count':total,'chapter_count':chapter_count,'size_indexes':size_count}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('book',type=Path)
    parser.add_argument('index',type=Path,nargs='?')
    args=parser.parse_args()
    try: result=validate_book(args.book,args.index)
    except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
        parser.exit(1,'Invalid: '+str(exc)+'\n')
    print('Valid:',result['title'],'—',result['word_count'],'words,',result['chapter_count'],
          'chapters,',result['size_indexes'],'size indexes')

if __name__=='__main__': main()
