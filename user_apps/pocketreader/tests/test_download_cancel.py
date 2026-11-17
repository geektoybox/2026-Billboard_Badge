"""Cancellation returns to the library instead of surfacing a book error."""
import ast
import asyncio
from pathlib import Path
import types
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'overlay/apps/pocketreader.py'
cls=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='PocketReaderApp')
method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_open_selected')

class DownloadCancel(unittest.IsolatedAsyncioTestCase):
    async def test_blank_download_timeout_shows_stage_and_reason(self):
        wifi = types.SimpleNamespace(begin_operation=lambda: None, end_operation=lambda: None)
        library = types.SimpleNamespace(load_progress=lambda: {}, has_download=lambda entry: False,
                                        cloud_enabled=lambda: False)
        scope = {'wifi_setup': wifi, 'book_library': library, 'aio': asyncio}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app.books = [{'id': 'one', 'title': 'One', 'package': 'one.prb', '_starter': True}]
        app.selected = 0; app._download_task = True
        app._load_cached_catalog = app._close_reader = app._show_selection_led = lambda: None
        async def timeout(entry): raise TimeoutError()
        app._download_starter_book = timeout
        await app._open_selected()
        self.assertEqual(app.update_status, 'BOOK ERROR: DOWNLOADING: CONNECTION TIMED OUT. CHECK WI-FI AND RETRY.')
        self.assertIsNone(app._download_task)

    async def test_blank_error_includes_exception_name(self):
        wifi = types.SimpleNamespace(begin_operation=lambda: None, end_operation=lambda: None)
        library = types.SimpleNamespace(load_progress=lambda: {}, has_download=lambda entry: False,
                                        cloud_enabled=lambda: False)
        scope = {'wifi_setup': wifi, 'book_library': library, 'aio': asyncio}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app.books = [{'id': 'one', 'title': 'One', 'package': 'one.prb', '_starter': True}]
        app.selected = 0; app._download_task = True
        app._load_cached_catalog = app._close_reader = app._show_selection_led = lambda: None
        async def failure(entry): raise ValueError()
        app._download_starter_book = failure
        await app._open_selected()
        self.assertEqual(app.update_status, 'BOOK ERROR: DOWNLOADING: ValueError')

    async def test_index_memory_error_frees_reader_and_explains_desktop_index(self):
        events = []
        class Reader:
            def __init__(self, entry):
                self.header = {'schema': 2}; self.pages = [1] * 100
                self._compact_notes = {'f1': 'note'}; self._compact_page_details = {'0': {}}
            def close(self): pass
            def has_index(self, *args): return True
            async def prepare(self, *args): raise MemoryError()
        library = types.SimpleNamespace(load_progress=lambda: {}, has_download=lambda entry: True,
            BookPackage=Reader, installed_state=lambda: {'one': {'sha256': 'hash'}},
            text_size=lambda: 'medium', font_family=lambda: 'tinos')
        wifi = types.SimpleNamespace(begin_operation=lambda: events.append('begin'), end_operation=lambda: events.append('end'))
        scope = {'wifi_setup': wifi, 'book_library': library, 'aio': asyncio,
                 'gc': types.SimpleNamespace(collect=lambda: events.append('collect'))}
        code = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[]))
        exec(compile(code, str(SOURCE), 'exec'), scope)
        app = scope['App'](); app.books = [{'id': 'one', 'package': 'one.prb'}]; app.selected = 0
        app.reader = None; app._download_task = True
        def close():
            self.assertEqual(app.reader.pages, [])
            self.assertEqual(app.reader._compact_notes, {})
            self.assertEqual(app.reader._compact_page_details, {})
            app.reader = None; events.append('close')
        app._close_reader = close
        app._show_selection_led = lambda: events.append('selection')
        await app._open_selected()
        self.assertIn('USE THE DESKTOP APP TO GENERATE THE INDEX', app.update_status)
        self.assertIn('RESTART THE BADGE', app.update_status)
        self.assertTrue(app._dirty)
        self.assertIsNone(app._download_task)
        self.assertEqual(events, ['begin', 'close', 'collect', 'selection', 'end'])

    async def test_cancel_returns_to_library_and_releases_operation(self):
        events=[];started=asyncio.Event()
        wifi=types.SimpleNamespace(begin_operation=lambda:events.append('begin'),end_operation=lambda:events.append('end'))
        library=types.SimpleNamespace(load_progress=lambda:{},has_download=lambda entry:False)
        scope={'wifi_setup':wifi,'book_library':library,'aio':asyncio}
        code=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App',bases=[],keywords=[],body=[method],decorator_list=[])],type_ignores=[]))
        exec(compile(code,str(SOURCE),'exec'),scope);app=scope['App']()
        app.books=[{'id':'one','title':'One','package':'one.prb','_starter':True}];app.selected=0
        app._load_cached_catalog=lambda:events.append('reload')
        app._close_reader=lambda:events.append('close')
        app._show_selection_led=lambda:events.append('selection')
        async def download(entry):
            try:
                started.set()
                await asyncio.Event().wait()
            finally: events.append('cleanup')
        app._download_starter_book=download
        task=asyncio.create_task(app._open_selected());app._download_task=task
        await started.wait();self.assertTrue(app._download_cancellable)
        task.cancel();await task
        self.assertEqual(app.update_status,'DOWNLOAD CANCELED')
        self.assertFalse(app._download_cancellable);self.assertIsNone(app._download_task)
        self.assertEqual(events,['begin','cleanup','reload','close','selection','end'])

if __name__=='__main__':unittest.main()
