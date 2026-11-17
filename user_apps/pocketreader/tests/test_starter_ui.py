"""The optional starter catalog coexists with local books and Wi-Fi controls."""
import ast
import asyncio
from pathlib import Path
import types
import unittest

PATH=Path(__file__).resolve().parents[1]/'overlay/apps/pocketreader.py'
tree=ast.parse(PATH.read_text())
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PocketReaderApp')

def load(names,scope):
    methods=[n for n in cls.body if isinstance(n,(ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    code=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='App',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[]))
    exec(compile(code,str(PATH),'exec'),scope);return scope['App']

class StarterUI(unittest.TestCase):
    def test_available_books_merge_and_disable_keeps_local(self):
        state={'enabled':True};local=[{'id':'local','title':'Local','package':'local.prb'}]
        starter=types.SimpleNamespace(load_catalog=lambda: [{'id':'pg1','title':'Public','author':'Author'}] if state['enabled'] else [])
        reader=types.SimpleNamespace(has_download=lambda entry: False,local_books=lambda: local,sort_books=lambda entries:sorted(entries,key=lambda e:e['title']))
        app=load({'_set_catalog','_show_library_page','_can_redownload'},{'book_library':reader,'starter_library':starter,'REFRESH_ROW':8})()
        app.library_page=0;app.selected=0;app._set_catalog({'books':[]})
        self.assertEqual([x['id'] for x in app.all_books],['local','pg1'])
        self.assertTrue(app.all_books[1]['_starter']);self.assertEqual(app.all_books[1]['package'],'pg1.prb')
        self.assertTrue(app._can_redownload({'id':'pg1'}));self.assertFalse(app._can_redownload({'id':'local'}))
        state['enabled']=False;app._set_catalog({'books':[]});self.assertEqual([x['id'] for x in app.all_books],['local'])
        self.assertFalse(app._can_redownload({'id':'pg1'}))

    def test_toggle_is_distinct_from_wifi_and_does_not_auto_connect(self):
        state={'enabled':False};starter=types.SimpleNamespace(enabled=lambda:state['enabled'],set_enabled=lambda v:state.update(enabled=v))
        class Button:
            state=False
            def pressed(self): value=self.state; self.state=False; return value
        back,up,down,select=Button(),Button(),Button(),Button()
        scope={'starter_library':starter,'PhysicalMenuPlus':back,'PhysicalMenuMinus':up,'UserB':down,'UserA':select}
        app=load({'_wifi_main_items','_run_settings_menu'},scope)()
        app._wifi_networks=[{'ssid':'Home'}];app._library_menu=True;app._led_menu=False;app._settings_cursor=2
        app._suppress_chord=False;app._settings_inline=None;app._sleep_options_menu=False
        app._font_options_menu=False;app._family_options_menu=False;app._menu_update_pending=False
        app._settings_row_pending=0;app._dirty=False;app.reader_display=types.SimpleNamespace(ready=lambda:False)
        app._queue_settings_rows=lambda *rows:None;app._load_cached_catalog=lambda:None;app._catalog_pending=False
        self.assertEqual(app._wifi_main_items()[7][0],'login')
        self.assertNotIn('starter',[item[0] for item in app._wifi_main_items() if item])
        select.state=True;app._run_settings_menu();self.assertTrue(state['enabled']);self.assertFalse(app._catalog_pending)
        self.assertEqual(app.update_status, 'USE REFRESH LIST TO UPDATE LIBRARY')
        select.state=True;app._run_settings_menu();self.assertFalse(state['enabled'])

    def test_library_sort_group_and_auto_page_controls(self):
        state={'order':'title','group':False,'auto':False}
        class Button:
            state=False
            def pressed(self): value=self.state;self.state=False;return value
        back,up,down,select=Button(),Button(),Button(),Button()
        def set_sort(order,group): state.update(order=order,group=group)
        library=types.SimpleNamespace(library_sort_settings=lambda:(state['order'],state['group']),
            set_library_sort_settings=set_sort,library_auto_page_enabled=lambda:state['auto'],
            set_library_auto_page_enabled=lambda value:state.update(auto=value))
        scope={'book_library':library,'PhysicalMenuPlus':back,'PhysicalMenuMinus':up,'UserB':down,'UserA':select}
        app=load({'_run_settings_menu'},scope)()
        app._library_menu=True;app._led_menu=False;app._settings_cursor=0;app._suppress_chord=False
        app._settings_inline=None;app._sleep_options_menu=app._font_options_menu=app._family_options_menu=False
        app._menu_update_pending=False;app._settings_row_pending=0;app._dirty=False
        app.reader_display=types.SimpleNamespace(ready=lambda:False)
        app._queue_settings_rows=lambda *rows:None;app._load_cached_catalog=lambda:None
        app._show_settings_led=lambda:None;app._menu_selection_changed=lambda:None
        select.state=True;app._run_settings_menu();self.assertEqual(app._settings_inline,'sort')
        down.state=True;app._run_settings_menu()
        select.state=True;app._run_settings_menu();self.assertEqual(state['order'],'author_first')
        app._settings_cursor=1;select.state=True;app._run_settings_menu();self.assertTrue(state['group'])
        app._settings_cursor=3;select.state=True;app._run_settings_menu();self.assertTrue(state['auto'])
        back.state=True;app._run_settings_menu();self.assertFalse(app._library_menu);self.assertEqual(app._settings_cursor,3)

    def test_cloud_icon_for_remote_and_book_icon_for_download(self):
        app=load({'_draw_book_state_icon'}, {'BLACK':1})()
        class Canvas:
            def __init__(self): self.rectangles=[]; self.fills=[]
            def rect(self,*args): self.rectangles.append(args)
            def fill_rect(self,*args): self.fills.append(args)
        remote=Canvas(); local=Canvas()
        app._draw_book_state_icon(remote,0,0,False)
        app._draw_book_state_icon(local,0,0,True)
        self.assertGreater(len(remote.fills),5)
        self.assertEqual(remote.rectangles,[])
        self.assertEqual(len(local.rectangles),1)

    def test_menu_plus_cancels_active_starter_download(self):
        events=[]
        wifi=types.SimpleNamespace(settings_generation=lambda:0)
        transfer=types.SimpleNamespace(generation=lambda:0)
        clock=types.SimpleNamespace(ticks_ms=lambda:123)
        button=types.SimpleNamespace(pressed=lambda:True)
        App=load({'run'}, {'wifi_setup':wifi,'book_transfer':transfer,'time':clock,'PhysicalMenuPlus':button})
        app=App();app.launched=False;app.detail=False
        app._transfer_generation=app._web_settings_generation=0;app._exit_pending=False
        app._update_library_idle_lights=lambda:None
        app._download_task=types.SimpleNamespace(cancel=lambda:events.append('cancel'))
        app._download_cancellable=True; app._dirty=False
        app.run()
        self.assertEqual(events,['cancel'])
        self.assertFalse(app._download_cancellable)

    def test_wifi_menu_responds_while_connection_or_catalog_runs(self):
        wifi=types.SimpleNamespace(settings_generation=lambda:0,expire_if_due=lambda:None)
        transfer=types.SimpleNamespace(generation=lambda:0)
        clock=types.SimpleNamespace(ticks_ms=lambda:123)
        App=load({'run'}, {'wifi_setup':wifi, 'book_transfer':transfer, 'time':clock})
        for active in ('_wifi_task','_catalog_task'):
            app=App(); app.launched=False; app.detail=False
            app._transfer_generation=app._web_settings_generation=0
            app._exit_pending=app._setup_pending=app._sleep_pending=app._sleep_requested=False
            app._download_task=app._wifi_task=app._catalog_task=None
            setattr(app,active,object())
            app._update_library_idle_lights=lambda:None
            app._handle_menu_sleep_chord=lambda:False
            app._update_device_sleep_timer=lambda:False
            app._catalog_pending=False;app._wifi_menu=True
            calls=[];app._run_wifi_menu=lambda:calls.append('responded')
            app.run()
            self.assertEqual(calls,['responded'])

    def test_existing_wifi_connection_is_not_restarted(self):
        app=load({'_queue_wifi_connection'}, {})()
        task=object(); app._wifi_task=task
        app._queue_wifi_connection('Another network',None)
        self.assertIs(app._wifi_task,task)
        self.assertEqual(app.update_status,'WIFI CONNECTING')

    def test_connecting_wifi_does_not_refresh_catalog(self):
        async def connect(*args): pass
        wifi = types.SimpleNamespace(connect_saved=connect)
        app = load({'_connect_wifi'}, {'wifi_setup': wifi})()
        app._wifi_menu = False; app._catalog_pending = False
        app._wifi_task = object(); app._dirty = False; app.update_status = 'CONNECTING HOME'
        app._update_wifi_indicator = lambda: None
        asyncio.run(app._connect_wifi('HOME', None))
        self.assertFalse(app._catalog_pending)
        self.assertEqual(app.update_status, '')

if __name__=='__main__':unittest.main()
