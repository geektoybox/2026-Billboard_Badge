"""Shared selection refresh and Wi-Fi menu behavior."""
import ast
from pathlib import Path
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'overlay/apps/pocketreader.py'


def load_methods(names, scope):
    tree = ast.parse(SOURCE.read_text())
    app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PocketReaderApp')
    methods = [n for n in app.body if isinstance(n, ast.FunctionDef) and n.name in names]
    code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[]))
    exec(compile(code, str(SOURCE), 'exec'), scope)
    return scope['App']


class FastMenus(unittest.TestCase):
    def test_font_and_size_previews_are_centered_and_spaced(self):
        class Font:
            def __init__(self, height): self.line_height = height
            def measure(self, text): return len(text) * 8
        families = {family: {size: (Font(height), 0, 0) for size, height in zip(('small','medium','large','xl'), heights)}
                    for family, heights in {'opendyslexic': (32,48,56,80), 'ubuntu': (24,32,40,56), 'tinos': (24,32,40,56)}.items()}
        library = types.SimpleNamespace(font_family=lambda:'opendyslexic', text_size=lambda:'medium')
        scope = {'book_library': library, 'READER_FAMILIES': families, 'LIBRARY_ROW_Y':96,
                 'LIBRARY_ROW_HEIGHT':96, 'LIBRARY_FRAME_HEIGHT':84, 'PORTRAIT_WIDTH':672,
                 'BLACK':1, 'HACK16':Font(16)}
        App = load_methods(('_draw_settings_row',), scope)
        app = App();app._led_menu=app._library_menu=False;app._settings_cursor=0
        app._idle_light_minutes=0;app._light_brightness='medium';app._availability_lights=False
        app._quick_boot_enabled=False;app._settings_inline=None
        calls=[]
        canvas=types.SimpleNamespace(rect=lambda *args:None, fill_rect=lambda *args:None,
                                     text=lambda *args:calls.append(args))
        app._draw_settings_row(canvas,0)
        for label in ('OpenDyslexic','Ubuntu','Tinos'):
            call=next(item for item in calls if item[0]==label);font=call[-1]
            self.assertLessEqual(abs(84-2*(call[2]-96)-font.line_height), 15)
        calls.clear();app._draw_settings_row(canvas,1)
        choices=[next(item for item in calls if item[0]==label) for label in ('Small','Medium','Large','XLarge')]
        self.assertGreater(choices[0][1],110)
        for call in choices:
            font=call[-1];self.assertLessEqual(abs(84-2*(call[2]-192)-font.line_height), 15)

    def test_bookmark_refresh_is_partial_only_on_the_same_book_page(self):
        queued = []
        display = types.SimpleNamespace(black=None, red=None, clear=lambda: None)
        scope = {'display': display, 'PortraitCanvas': lambda *_: object()}
        App = load_methods(('_draw',), scope)
        app = App(); app.reader_display = types.SimpleNamespace(queue_frame=lambda **kw: queued.append(kw))
        app._screen_identity = lambda: ('book',); app._drawn_screen = ('book',)
        app._read_battery = app._draw_detail = app._draw_wifi_indicator = lambda *args: None
        app._menu_snapshot = lambda: None
        app._show_reading_progress = lambda: None
        app._wifi_menu = app._settings_menu = app._toc_menu = app._bookmark_menu = False
        app.detail = True; app.reader = types.SimpleNamespace(entry={'id': 'one'}); app.current_page = 2
        app._small_update = True; app._bookmark_partial = ('one', 2)
        app._draw(); self.assertEqual(queued[-1], {'partial': True})
        app._small_update = True; app._bookmark_partial = ('one', 2); app.current_page = 3
        app._draw(); self.assertEqual(queued[-1], {'partial': False})

    def test_settings_wifi_chapters_bookmarks_share_fast_path(self):
        rectangles = []; queued = []; ready = [True]
        class Canvas:
            def __init__(self, *_): pass
            def rect(self, *args): rectangles.append(args)
        scope = {'PortraitCanvas': Canvas, 'display': types.SimpleNamespace(black=None, red=None),
                 'LIBRARY_ROW_Y': 96, 'LIBRARY_ROW_HEIGHT': 96, 'LIBRARY_FRAME_HEIGHT': 84,
                 'PORTRAIT_WIDTH': 672, 'BLACK': 1, 'WHITE': 0}
        App = load_methods(('_menu_snapshot', '_menu_selection_changed', '_flush_menu_selection'), scope)
        app = App(); app.reader_display = types.SimpleNamespace(ready=lambda: ready[0], queue_frame=lambda **kwargs: queued.append(kwargs))
        app._wifi_menu = app._settings_menu = app._toc_menu = app._bookmark_menu = False
        app._sleep_options_menu = app._font_options_menu = app._family_options_menu = False
        app._wifi_view = 'main'; app._wifi_cursor = app._settings_cursor = app._toc_cursor = app._bookmark_cursor = 0
        app._chapters = [1] * 10; app._bookmarks = [1] * 10
        for flag, cursor in (('_settings_menu', '_settings_cursor'), ('_wifi_menu', '_wifi_cursor'),
                             ('_toc_menu', '_toc_cursor'), ('_bookmark_menu', '_bookmark_cursor')):
            setattr(app, flag, True); setattr(app, cursor, 0)
            app._dirty = False; app._menu_update_pending = False; app._drawn_menu = app._menu_snapshot()
            setattr(app, cursor, 1); app._menu_selection_changed()
            self.assertEqual(queued[-1], {'partial': True})
            self.assertEqual(app._drawn_menu[1], 1)
            self.assertFalse(app._dirty)
            setattr(app, flag, False)
        app._settings_menu = True; app._settings_cursor = 1; app._drawn_menu = app._menu_snapshot()
        ready[0] = False; app._settings_cursor = 2; app._menu_selection_changed()
        app._settings_cursor = 3; app._menu_selection_changed()
        self.assertTrue(app._menu_update_pending)
        ready[0] = True; self.assertTrue(app._flush_menu_selection())
        self.assertEqual(app._drawn_menu[1], 3)
        app._settings_menu = False; app._toc_menu = True; app._toc_cursor = 7
        app._drawn_menu = app._menu_snapshot(); app._toc_cursor = 8
        app._menu_selection_changed()
        self.assertTrue(app._dirty)  # A new page needs its contents redrawn.

    def test_wifi_availability_setting_and_back_to_settings(self):
        class LEDs(dict):
            def pause(self): pass
            def write(self): pass
        class Button:
            value = False
            def __init__(self, pressed=False): self.state = pressed
            def pressed(self): value = self.state; self.state = False; return value
        leds = LEDs(); back = Button(); up = Button(); a = Button(); b = Button()
        scope = {'leds': leds, 'ROW_LED_MAP': (7,6,5,4,3,2,1,0),
                 'PhysicalMenuPlus': back, 'PhysicalMenuMinus': up, 'UserA': a, 'UserB': b}
        App = load_methods(('_show_wifi_led', '_wifi_item_count', '_wifi_main_items', '_run_wifi_menu'), scope)
        app = App(); app._wifi_view = 'main'; app._wifi_cursor = 0
        app._wifi_networks = [{'ssid': 'home'}]; app._light_level = lambda: 2
        app._availability_lights = False; app._show_wifi_led()
        self.assertEqual(leds[7], (2,2,2)); self.assertTrue(all(leds[i] == (0,0,0) for i in range(7)))
        app._availability_lights = True; app._show_wifi_led()
        self.assertEqual(leds[6], (0,0,2)); self.assertEqual(leds[0], (0,0,2))
        app._suppress_chord = False; app.update_status = ''; app._show_settings_led = lambda: None
        back.state = True; app._run_wifi_menu()
        self.assertFalse(app._wifi_menu); self.assertTrue(app._settings_menu); self.assertEqual(app._settings_cursor, 4)
