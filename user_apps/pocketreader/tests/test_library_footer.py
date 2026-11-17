"""Check that library actions and page number fit beneath eight book slots."""
import ast
from pathlib import Path
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'overlay/apps/pocketreader.py'

class LibraryFooter(unittest.TestCase):
    def test_bottom_hint_uses_button_label(self):
        tree = ast.parse(SOURCE.read_text())
        app = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'PocketReaderApp')
        method = next(item for item in app.body if isinstance(item, ast.FunctionDef) and item.name == '_draw_bottom_hints')
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        scope = {'BLACK': 1}; exec(compile(code, str(SOURCE), 'exec'), scope)
        class Canvas:
            def __init__(self): self.texts = []; self.rotated = []
            def rect(self, *args): pass
            def text(self, label, *args): self.texts.append(label)
        app = scope['App'](); app._bookmark_menu = app._toc_menu = False
        canvas = Canvas(); app._draw_bottom_hints(canvas, reader=True)
        self.assertIn('HOLD BKMRK FOR LIST', canvas.texts)
        self.assertNotIn('HOLD BOOKMARK FOR LIST', canvas.texts)

    def test_remove_choice_uses_fast_update_and_falls_back_when_busy(self):
        tree = ast.parse(SOURCE.read_text())
        source = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'PocketReaderApp')
        methods = [item for item in source.body if isinstance(item, ast.FunctionDef) and item.name in ('_draw_remove_choices', '_update_remove_choices')]
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
        events = []
        class Canvas:
            def fill_rect(self, *args): events.append(('clear', args))
            def text(self, *args): events.append(('text', args[0]))
        scope = {'BLACK': 1, 'WHITE': 0, 'display': types.SimpleNamespace(black=None, red=None), 'PortraitCanvas': lambda *_: Canvas()}
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app._remove_yes = True; app._dirty = False; app._drawn_screen = ('remove',)
        app.reader_display = types.SimpleNamespace(ready=lambda: True, queue_frame=lambda partial: events.append(('partial', partial)))
        app._update_remove_choices()
        self.assertIn(('text', '[ YES ]'), events)
        self.assertIn(('partial', True), events)
        self.assertFalse(app._dirty)
        app.reader_display.ready = lambda: False
        app._update_remove_choices()
        self.assertTrue(app._dirty)
        self.assertTrue(app._small_update)

    def test_recent_badge_is_restored_after_selection_border_moves(self):
        tree = ast.parse(SOURCE.read_text())
        source = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'PocketReaderApp')
        method = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == '_update_library_border')
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        canvas = object(); events = []
        scope = {'REFRESH_ROW': 8, 'LIBRARY_ROW_Y': 96, 'LIBRARY_ROW_HEIGHT': 96,
                 'display': types.SimpleNamespace(black=None, red=None), 'PortraitCanvas': lambda *_: canvas}
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app._drawn_library_selection = 0; app._drawn_library_choice = 0
        app._drawn_recent_row = 0; app.selected = 1; app._bottom_choice = 0
        app._draw_book_border = lambda *args, **kwargs: events.append(('border', args[1]))
        app._draw_recent_badge = lambda c, y: events.append(('recent', y))
        app.reader_display = types.SimpleNamespace(queue_frame=lambda partial: events.append(('frame', partial)))
        self.assertTrue(app._update_library_border())
        self.assertEqual(events[-2:], [('recent', 96), ('frame', True)])
        events.clear(); app.selected = 0
        self.assertTrue(app._update_library_border())
        self.assertEqual(events[-2:], [('recent', 96), ('frame', True)])

    def test_download_progress_is_drawn_while_transfer_runs(self):
        tree = ast.parse(SOURCE.read_text())
        app = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'PocketReaderApp')
        run = next(item for item in app.body if isinstance(item, ast.FunctionDef) and item.name == 'run')
        block = next(item for item in run.body if isinstance(item, ast.If) and ast.unparse(item.test) == 'self._download_task is not None')
        method = next(item for item in app.body if isinstance(item, ast.FunctionDef) and item.name == '_show_download_progress')
        tick = ast.FunctionDef(name='tick', args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='self')], kwonlyargs=[], kw_defaults=[], defaults=[]), body=[block], decorator_list=[])
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[tick, method], decorator_list=[])], type_ignores=[]))
        scope = {'time': types.SimpleNamespace(ticks_ms=lambda: 1)}
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app._download_task = object(); app._download_cancellable = False
        app._download_percent = 0; app._dirty = False
        app.reader_display = types.SimpleNamespace(ready=lambda: True)
        drawn = []; app._draw = lambda: drawn.append(app._download_percent)
        app._show_download_progress(42, 100); app.tick()
        self.assertEqual(drawn, [42])
        self.assertTrue(app._small_update)

    def test_page_number_and_actions_share_footer(self):
        tree = ast.parse(SOURCE.read_text())
        app_class = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'PocketReaderApp')
        method = next(item for item in app_class.body if isinstance(item, ast.FunctionDef) and item.name == '_draw_library')
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        class Font:
            def measure(self, text): return len(text) * 12
        font = Font()
        class Canvas:
            def __init__(self): self.texts = []; self.rotated = []
            def text(self, *args):
                if args[2] % 8:
                    raise ValueError('bitmap text y must align to an 8-pixel boundary')
                self.texts.append(args)
            def wrapped_text(self, text, x, y, width, scale, color, line_gap=0):
                self.text(text, x, y, scale, color, width)
            def rect(self, *args): pass
            def fill_rect(self, *args): pass
            def wordmark(self, *args): pass
            def text_ccw(self, *args): self.rotated.append(args)
        lib = types.SimpleNamespace(load_progress=lambda ids: {}, most_recent_read=lambda _: None,
                                    installed_state=lambda: {}, _exists=lambda path: True,
                                    package_path=lambda entry: entry['id'])
        scope = {'book_library': lib, 'PORTRAIT_WIDTH': 672, 'BLACK': 1, 'WHITE': 0, 'RED': 2,
                 'REFRESH_ROW': 8, 'LIBRARY_ROW_Y': 96, 'LIBRARY_ROW_HEIGHT': 96, 'LIBRARY_FRAME_HEIGHT': 84,
                 'OPEN_DYSLEXIC24': font, 'HACK16': font, 'HACK24': font}
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app.books = []; app.all_books = [{} for _ in range(9)]
        app.library_page = 0; app.selected = 8; app._bottom_choice = 0
        app._remove_prompt = app._bottom_menu = False; app.update_status = 'LIBRARY UPDATED'
        app._draw_navigation_hints = app._draw_battery = app._draw_bottom_hints = lambda *_: None
        app._draw_footer_border = lambda *_: None
        canvas = Canvas(); app._draw_library(canvas)
        label = next(item for item in canvas.texts if item[0] == 'PAGE 1/2')
        self.assertEqual(label[1], 672 - 32 - font.measure(label[0]))
        self.assertEqual(label[2], 872)
        self.assertTrue(any(item[0] == 'REFRESH LIST' and item[2] == 872 for item in canvas.texts))
        self.assertTrue(any(item[0] == 'SETTINGS' and item[2] == 872 for item in canvas.texts))
        self.assertFalse(any(item[0] == 'LIBRARY UPDATED' for item in canvas.texts))
        self.assertTrue(any(item[0] == 'HOLD UP/DOWN FOR MORE BOOKS' and item[2] == 936 for item in canvas.texts))
        app._catalog_task = object(); app.update_status = 'REFRESHING LIBRARY'
        canvas = Canvas(); app._draw_library(canvas)
        self.assertTrue(any(item[0] == 'REFRESHING LIBRARY' and item[2] == 408 for item in canvas.texts))
        app._catalog_task = None
        app.update_status = 'BOOK ERROR: Download size differs from the current catalog'
        canvas = Canvas(); app._draw_library(canvas)
        self.assertTrue(any(item[0] == app.update_status and item[2] == 352 for item in canvas.texts))
        self.assertTrue(any(item[0] == 'UP/DOWN TO DISMISS' for item in canvas.texts))
        app.update_status = ''
        app.books = [{'id': str(i), 'title': 'Book {}'.format(i), 'author': 'Author', 'package': 'book.prb'} for i in range(8)]
        app.all_books = app.books + [{'id': 'extra'}]
        app._draw_book_border = lambda *_: None
        app._draw_recent_badge = lambda canvas, y: canvas.text_ccw('RECENT', 618, y + (84 - font.measure('RECENT')) // 2, 1, 0)
        app._draw_book_state_icon = lambda *_: None
        canvas = Canvas(); app._draw_library(canvas)
        self.assertEqual(len([item for item in canvas.texts if item[0].startswith('Book ')]), 8)
        self.assertTrue(any(item[0] == 'PAGE 1/2' for item in canvas.texts))
        app.selected = 2; app._download_task = object(); app._download_percent = 42
        app.update_status = 'DOWNLOADING Book 2'
        canvas = Canvas(); app._draw_library(canvas)
        row_y = 96 + 2 * 96 + 48
        status = next(item for item in canvas.texts if item[0] == 'DOWNLOADING')
        self.assertEqual(status[2], row_y)
        number = next(item for item in canvas.texts if item[0] == '42%')
        for value in (0, 1, 10, 100):
            app._download_percent = value
            updated = Canvas(); app._draw_library(updated)
            self.assertEqual(next(item[1] for item in updated.texts if item[0] == 'DOWNLOADING'), status[1])
            self.assertEqual(next(item[1] for item in updated.texts if item[0] == '{}%'.format(value) and item[2] == row_y and item[1] != 590), number[1])
        self.assertTrue(any(item[0] == 'Author' and item[2] == row_y for item in canvas.texts))
        self.assertTrue(any(item[0] == '0%' and item[1] == 590 and item[2] == row_y
                            for item in canvas.texts))
        self.assertFalse(any(item[0] == 'DOWNLOADING Book 2' for item in canvas.texts))
        app.update_status = 'PREPARING Book 2'
        canvas = Canvas(); app._draw_library(canvas)
        self.assertTrue(any(item[0] == 'PREPARING' and item[2] == row_y for item in canvas.texts))
        lib.most_recent_read = lambda _: 2
        app._download_task = None; app.update_status = ''
        canvas = Canvas(); app._draw_library(canvas)
        self.assertTrue(any(item[0] == '0%' and item[1] == 558 and item[2] == row_y
                            for item in canvas.texts))
        self.assertIn(('RECENT', 618, 96 + 2 * 96 + (84 - font.measure('RECENT')) // 2, 1, 0), canvas.rotated)
