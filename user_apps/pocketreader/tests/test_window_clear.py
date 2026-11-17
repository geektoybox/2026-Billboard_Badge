"""Checks for two targeted white drives before the refresh notice."""
import ast
import asyncio
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]

class WindowClear(unittest.TestCase):
    def test_force_white_region_preserves_pixels_outside_the_window(self):
        path = ROOT / 'overlay/lib/pocketreader_display.py'
        tree = ast.parse(path.read_text())
        fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == '_force_white_region')
        code = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        scope = {}; exec(compile(code, str(path), 'exec'), scope)
        buf = bytearray(960 * 672 // 8); shown = bytearray(buf)
        for _ in range(2):
            scope['_force_white_region'](buf, shown, (450, 75, 160, 2))
            for row in (75, 76):
                offset = row * 120
                self.assertEqual(shown[offset + 56], 0xfc)
                self.assertEqual(shown[offset + 76], 0x03)
                self.assertTrue(all(x == 255 for x in shown[offset + 57:offset + 76]))
                self.assertEqual(shown[offset + 55], 0)
                self.assertEqual(shown[offset + 77], 0)
            shown[:] = buf  # The panel records the completed white frame.
        buf[75 * 120 + 56] = 4
        with self.assertRaises(ValueError):
            scope['_force_white_region'](buf, shown, (450, 75, 160, 2))

    def test_window_is_cleared_twice_before_the_notice(self):
        path = ROOT / 'overlay/apps/pocketreader.py'
        tree = ast.parse(path.read_text())
        app = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'PocketReaderApp')
        method = next(x for x in app.body if isinstance(x, ast.AsyncFunctionDef) and x.name == '_clear_refresh_window')
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        events = []
        class Canvas:
            def __init__(self, *_): pass
            def fill_rect(self, *args): events.append(('white', args))
            @staticmethod
            def _physical_rect(*_): return (450, 75, 160, 530)
        scope = {'aio': types.SimpleNamespace(sleep_ms=lambda _: asyncio.sleep(0)), 'PortraitCanvas': Canvas,
                 'display': types.SimpleNamespace(black=None, red=None), 'WHITE': 0}
        exec(compile(code, str(path), 'exec'), scope)
        app = scope['App'](); app.reader_display = types.SimpleNamespace(ready=lambda: True,
            queue_frame=lambda **kwargs: events.append(('frame', kwargs)))
        asyncio.run(app._clear_refresh_window())
        self.assertEqual(events[0][0], 'white')
        self.assertEqual(events[1:], [('frame', {'partial': True, 'force_white_region': (450, 75, 160, 530)})] * 2)
        self.assertFalse(app._refresh_preclear)
