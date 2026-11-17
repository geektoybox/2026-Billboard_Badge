"""The badge draws supported book-format styles using the right font and alignment."""
import ast
from pathlib import Path
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'overlay/apps/pocketreader.py'
tree = ast.parse(SOURCE.read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PocketReaderApp')
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_detail')
compact_method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_draw_compact_lines')
canvas_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PortraitCanvas')
canvas_text = next(n for n in canvas_class.body if isinstance(n, ast.FunctionDef) and n.name == 'text')
canvas_styled_text = next(n for n in canvas_class.body if isinstance(n, ast.FunctionDef) and n.name == 'styled_text')

class MarkupDisplay(unittest.TestCase):
    def test_small_caps_draw_uppercase_glyphs_at_reduced_size(self):
        class Plane:
            def __init__(self): self.blits = []; self.pixels = []
            def blit(self, *args): self.blits.append(args)
            def pixel(self, *args): self.pixels.append(args)
        class Glyph:
            def pixel(self, column, row): return True
        class Font:
            line_height = 16
            def __init__(self): self.chars = []
            def glyph(self, char): self.chars.append(char); return Glyph(), 8
            def measure(self, text): return 8 * len(text)
        scope = {'BLACK':1, 'PHYSICAL_WIDTH':480}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Canvas', bases=[], keywords=[], body=[canvas_text], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        canvas = scope['Canvas'](); canvas.black = Plane(); font = Font()
        canvas.text('Aa', 32, 80, font=font, small_caps=True)
        self.assertEqual(font.chars, ['A', 'A'])
        self.assertEqual(len(canvas.black.blits), 1)
        self.assertEqual(len(canvas.black.pixels), 12 * 6)

    def test_underline_draws_beneath_only_underlined_run(self):
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Canvas', bases=[], keywords=[], body=[canvas_styled_text], decorator_list=[])], type_ignores=[]))
        scope = {}
        exec(compile(code, str(SOURCE), 'exec'), scope)
        canvas = scope['Canvas'](); canvas.text = lambda *args, **kwargs: None
        marks = []; canvas.fill_rect = lambda *args: marks.append(args)
        font = types.SimpleNamespace(measure=lambda text: 8 * len(text), line_height=16)
        canvas.styled_text([('A', {}), ('BC', {'u': True}), ('D', {})], 32, 80, font)
        self.assertEqual(marks, [(40, 95, 16, 1)])

    def test_reader_dispatches_compact_book_lines(self):
        font = types.SimpleNamespace(measure=lambda text: 8 * len(text))
        scope = {'READER_FAMILIES': {'tinos': {'medium': (font, 32, 24)}},
                 'PORTRAIT_WIDTH': 672, 'BLACK': 1, 'RED': 2, 'HACK24': font, 'HACK16': font,
                 'book_library': types.SimpleNamespace(text_size=lambda: 'medium')}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App']()
        lines = [[('Chapter', {'heading': 'h1'})]]
        app.reader = types.SimpleNamespace(entry={'title': 'Book'}, header={'schema': 5}, size='medium',
                                          font_family='tinos', pages=[1], read_page=lambda _: (lines, 0))
        app.current_page = 0; app._bookmarks = []; app._chapters = []; app.update_status = ''
        app._draw_navigation_hints = app._draw_battery = app._draw_bottom_hints = lambda *args, **kwargs: None
        rendered = []
        app._draw_compact_lines = lambda canvas, actual, family, size: rendered.append((actual, family, size))
        canvas = types.SimpleNamespace(text=lambda *args, **kwargs: None, fill_rect=lambda *args, **kwargs: None)
        app._draw_detail(canvas)
        self.assertEqual(rendered, [(lines, 'tinos', 'medium')])

    def test_compact_rendering_aligns_and_stylizes_runs(self):
        class Font:
            def __init__(self, advance): self.advance = advance
            def measure(self, text): return len(text) * self.advance
        normal, large = Font(8), Font(12)
        families = {'tinos': {'small': (normal, 24, 32), 'medium': (normal, 32, 24),
                              'large': (large, 40, 19), 'xl': (large, 56, 14)}}
        scope = {'READER_FAMILIES': families, 'PORTRAIT_WIDTH': 672}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[compact_method], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        calls = []
        canvas = types.SimpleNamespace(text=lambda *args, **kwargs: calls.append((args, kwargs)))
        lines = [[('A', {'heading': 'h1', 'alignment': 'c', 'b': True}),
                  ('B', {'heading': 'h1', 'alignment': 'c', 'i': True})],
                 [('', {'_reserved': True})],
                 [('End', {'alignment': 'r'})]]
        scope['App']()._draw_compact_lines(canvas, lines, 'tinos', 'medium')
        self.assertEqual(calls[0][0][1], (672 - 24) // 2)
        self.assertTrue(calls[0][1]['bold'])
        self.assertTrue(calls[1][1]['italic'])
        self.assertEqual(calls[2][0][1], 672 - 32 - 24)

    def test_styled_runs_are_drawn_adjacent(self):
        scope = {}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Canvas', bases=[], keywords=[], body=[canvas_styled_text], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        canvas = scope['Canvas'](); calls = []
        canvas.text = lambda *args, **kwargs: calls.append((args, kwargs))
        font = types.SimpleNamespace(measure=lambda text: 8 * len(text))
        canvas.styled_text([('A', {'b': True}), ('BC', {'i': True})], 32, 80, font)
        self.assertEqual([call[0][1] for call in calls], [32, 40])
        self.assertTrue(calls[0][1]['bold'])
        self.assertTrue(calls[1][1]['italic'])

    def test_bold_and_italic_draw_glyphs(self):
        class Plane:
            def __init__(self): self.blits = []; self.pixels = []
            def blit(self, *args): self.blits.append(args)
            def pixel(self, *args): self.pixels.append(args)
        class Glyph:
            def pixel(self, column, row): return column == 0 and row == 0
        class Font:
            line_height = 16
            def glyph(self, character): return Glyph(), 8
            def measure(self, text): return len(text) * 8
        scope = {'BLACK': 1, 'PHYSICAL_WIDTH': 480}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Canvas', bases=[], keywords=[], body=[canvas_text], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        canvas = scope['Canvas'](); canvas.black = Plane(); font = Font()
        canvas.text('A', 32, 80, font=font, bold=True)
        self.assertEqual(len(canvas.black.blits), 2)
        canvas.text('A', 32, 80, font=font, bold=True, italic=True)
        self.assertEqual(len(canvas.black.pixels), 2)
        self.assertEqual(canvas.black.pixels[1][1], canvas.black.pixels[0][1] + 1)

    def test_italic_leans_right_on_the_portrait_display(self):
        class Plane:
            def __init__(self): self.pixels = []
            def pixel(self, *args): self.pixels.append(args)
        class Glyph:
            def pixel(self, column, row): return column in (0, 15) and row == 0
        class Font:
            line_height = 16
            def glyph(self, character): return Glyph(), 8
            def measure(self, text): return len(text) * 8
        scope = {'BLACK': 1, 'PHYSICAL_WIDTH': 480}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Canvas', bases=[], keywords=[], body=[canvas_text], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        canvas = scope['Canvas'](); canvas.black = Plane()
        canvas.text('I', 32, 80, font=Font(), italic=True)
        bottom, top = sorted(canvas.black.pixels)
        self.assertGreater(top[1], bottom[1])

    def test_headings_and_centered_lines_use_expected_fonts(self):
        class Font:
            def __init__(self, width): self.width=width
            def measure(self,text): return len(text)*self.width
        normal, large=Font(10),Font(15)
        families={'tinos':{'medium':(normal,32,24),'large':(large,40,19)}}
        class Canvas:
            def __init__(self): self.calls=[]
            def text(self,*args): self.calls.append(args)
            def fill_rect(self,*args): pass
        scope={'READER_FAMILIES':families,'PORTRAIT_WIDTH':672,'BLACK':1,'RED':2,'HACK24':normal,'HACK16':normal,
               'book_library':types.SimpleNamespace(text_size=lambda:'medium')}
        code=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[]))
        exec(compile(code,str(SOURCE),'exec'),scope);app=scope['App']()
        app.reader=types.SimpleNamespace(entry={'title':'Book'},header={'schema':4},size='medium',font_family='tinos',
            pages=[1],read_page=lambda page:(['\x1fHBig title','','\x1fCCentered','\x1fLLeft heading','','Body'],0))
        app.current_page=0;app._bookmarks=[];app._chapters=[];app.update_status=''
        app._draw_navigation_hints=app._draw_battery=app._draw_bottom_hints=lambda *args,**kwargs:None
        canvas=Canvas();app._draw_detail(canvas)
        title=next(item for item in canvas.calls if item[0]=='Big title')
        centered=next(item for item in canvas.calls if item[0]=='Centered')
        left=next(item for item in canvas.calls if item[0]=='Left heading')
        body=next(item for item in canvas.calls if item[0]=='Body')
        self.assertIs(title[-1],large);self.assertEqual(title[1],(672-large.measure('Big title'))//2)
        self.assertIs(centered[-1],normal);self.assertEqual(centered[1],(672-normal.measure('Centered'))//2)
        self.assertIs(left[-1],large);self.assertEqual(left[1],32)
        self.assertIs(body[-1],normal);self.assertEqual(body[1],32)

if __name__=='__main__': unittest.main()
