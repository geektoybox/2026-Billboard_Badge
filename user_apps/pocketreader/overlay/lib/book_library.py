"""Private PocketReader XL catalog, cache, package, and progress helpers."""

from array import array
import binascii
import gc
import asyncio as aio
import hashlib
import json
import network
import os
import struct
import time
from lib import pocketreader_font_data as font_data
from lib import pocketreader_pagination as pagination
from lib import wifi_setup


CONFIG_PATH = "/data/pocketreader_config.json"
CACHE_STATE_PATH = "/data/pocketreader_books.json"
PROGRESS_PATH = "/data/pocketreader_progress.json"
READING_STATE_PATH = "/data/pocketreader_reading_state.json"
LOCAL_CATALOG_PATH = "/data/pocketreader_local_books.json"
PREFERENCES_PATH = "/data/pocketreader_preferences.json"
BOOK_DIR = "/books"


class PagePositions:
    """Store page triples compactly while retaining list-like access."""
    def __init__(self):
        self._values = array('I')

    def append(self, page):
        for value in page:
            self._values.append(value)

    def clear(self):
        self._values = array('I')

    def __len__(self):
        return len(self._values) // 3

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError('Page index out of range')
        start = index * 3
        return [self._values[start], self._values[start + 1], self._values[start + 2]]

    def __iter__(self):
        for index in range(len(self)):
            yield self[index]

    def __eq__(self, other):
        try:
            return len(self) == len(other) and all(self[index] == other[index] for index in range(len(self)))
        except (TypeError, AttributeError):
            return False


class LibraryError(Exception):
    pass


def _read_json(path, fallback=None):
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def _write_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(value, handle)
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(temporary, path)


def _write_font_indexes(path, index, current_key):
    """Save indexes, shedding other fonts only when storage is exhausted."""
    sizes = index['sizes']
    family = current_key.split(':', 1)[0] if ':' in current_key else 'opendyslexic'
    candidates = [key for key in sizes if key != current_key and
                  (key.split(':', 1)[0] if ':' in key else 'opendyslexic') != family]
    candidates.sort(key=lambda key: len(sizes[key].get('pages', '')) if isinstance(sizes[key], dict) else 0,
                    reverse=True)
    temporary, backup = path + '.tmp', path + '.index-backup'
    if _exists(backup) and not _exists(path):
        os.rename(backup, path)
    if _exists(backup):
        raise OSError('An index backup needs recovery')
    while True:
        try:
            with open(temporary, 'w') as handle:
                json.dump(index, handle)
        except Exception as exc:
            try:
                os.remove(temporary)
            except OSError:
                pass
            error = getattr(exc, 'errno', None)
            if error is None and exc.args:
                error = exc.args[0]
            if not isinstance(exc, OSError) or error != 28 or not candidates:
                raise
            del sizes[candidates.pop(0)]
            continue
        break
    had_original = _exists(path)
    try:
        if had_original:
            os.rename(path, backup)
        try:
            os.rename(temporary, path)
        except OSError:
            if had_original:
                os.rename(backup, path)
            raise
        if had_original:
            os.remove(backup)
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False










def light_settings():
    settings = _read_json(PREFERENCES_PATH, {})
    if not isinstance(settings, dict):
        settings = {}
    brightness = settings.get("light_brightness", "medium")
    if brightness not in ("off", "dim", "medium", "bright"):
        brightness = "medium"
    return brightness, bool(settings.get("availability_lights", False))


def set_light_setting(key, value):
    if key == "light_brightness" and value not in ("off", "dim", "medium", "bright"):
        raise LibraryError("Invalid light brightness")
    if key == "availability_lights":
        value = bool(value)
    elif key != "light_brightness":
        raise LibraryError("Invalid light setting")
    settings = _read_json(PREFERENCES_PATH, {})
    if not isinstance(settings, dict):
        settings = {}
    settings[key] = value
    _write_json(PREFERENCES_PATH, settings)


def library_sort_settings():
    settings = _read_json(PREFERENCES_PATH, {})
    if not isinstance(settings, dict):
        settings = {}
    order = settings.get('library_sort', 'title')
    if order == 'author':
        order = 'author_last'
    if order not in ('title', 'author_first', 'author_last', 'added', 'opened'):
        order = 'title'
    return order, bool(settings.get('group_series', False))


def library_auto_page_enabled():
    settings = _read_json(PREFERENCES_PATH, {})
    return bool(settings.get('library_auto_page', False)) if isinstance(settings, dict) else False


def set_library_auto_page_enabled(enabled):
    settings = _read_json(PREFERENCES_PATH, {})
    if not isinstance(settings, dict):
        settings = {}
    settings['library_auto_page'] = bool(enabled)
    _write_json(PREFERENCES_PATH, settings)


def set_library_sort_settings(order, group_series):
    if order == 'author':
        order = 'author_last'
    if order not in ('title', 'author_first', 'author_last', 'added', 'opened'):
        raise LibraryError('Invalid library sort order')
    settings = _read_json(PREFERENCES_PATH, {})
    if not isinstance(settings, dict):
        settings = {}
    settings['library_sort'] = order
    settings['group_series'] = bool(group_series)
    _write_json(PREFERENCES_PATH, settings)


def mark_opened(book_id):
    book_id = _safe_id(book_id)
    state = _read_json(READING_STATE_PATH, {})
    state['sequence'] = int(state.get('sequence', 0)) + 1
    state.setdefault('opened', {})[book_id] = state['sequence']
    _write_json(READING_STATE_PATH, state)


def author_sort_parts(author):
    """Handle common surname-last and 'surname, given names' forms."""
    name = str(author or '').strip().lower()
    if not name or name in ('unknown', 'unknown author'):
        return ('\uffff', '\uffff')
    if ',' in name:
        family, given = name.split(',', 1)
        return (given.strip() or family.strip(), family.strip())
    parts = name.split()
    if len(parts) == 1:
        return (parts[0], parts[0])
    if parts[-1].rstrip('.') in ('jr', 'sr', 'ii', 'iii', 'iv') and len(parts) > 2:
        parts = parts[:-1]
    start = len(parts) - 1
    while start > 1 and parts[start - 1] in ('de', 'del', 'der', 'di', 'la', 'le', 'van', 'von'):
        start -= 1
    return (' '.join(parts[:start]), ' '.join(parts[start:]))


def sort_books(entries):
    """Sort without relying on a clock; older books with unknown order sort last."""
    order, group = library_sort_settings()
    reading = _read_json(READING_STATE_PATH, {})
    opened = reading.get('opened', {})
    installed = installed_state()
    added = reading.setdefault('added', {})
    changed = False
    for entry in entries:
        book_id = entry.get('id')
        downloaded = installed.get(book_id, {}).get('download_order', 0)
        if book_id and book_id not in added and downloaded:
            added[book_id] = int(downloaded)
            changed = True
    if changed:
        _write_json(READING_STATE_PATH, reading)
    def text(entry, key):
        return str(entry.get(key) or '').lower()
    def sort_key(entry):
        title = text(entry, 'title')
        if order in ('author_first', 'author_last'):
            given, family = author_sort_parts(entry.get('author'))
            primary = (given, family, title) if order == 'author_first' else (family, given, title)
        elif order == 'opened':
            primary = (-int(opened.get(entry.get('id'), 0)), title)
        elif order == 'added':
            primary = (-int(added.get(entry.get('id'), 0)), title)
        else:
            primary = (title,)
        return ((text(entry, 'series') or title,) + primary) if group else primary
    return sorted(entries, key=sort_key)








def load_catalog():
    return {"schema": 1, "books": []}




def _safe_id(book_id):
    if not book_id or len(book_id) > 96:
        raise LibraryError("Invalid book identifier")
    for character in book_id:
        if not ("a" <= character <= "z" or "A" <= character <= "Z" or "0" <= character <= "9" or character in "-_"):
            raise LibraryError("Invalid book identifier")
    return book_id


def package_path(entry):
    return BOOK_DIR + "/" + _safe_id(entry["id"]) + ".prb"


def installed_state():
    return _read_json(CACHE_STATE_PATH, {})


def local_books():
    return _read_json(LOCAL_CATALOG_PATH, {}).get("books", [])


def is_installed(entry):
    state = installed_state().get(entry.get("id"), {})
    return state.get("sha256") == entry.get("sha256") and _exists(package_path(entry))


def has_download(entry):
    return entry.get("id") in installed_state() and _exists(package_path(entry))


def text_size():
    size = _read_json(PREFERENCES_PATH, {}).get("text_size", "medium")
    return size if size in ("small", "medium", "large", "xl") else "medium"


def set_text_size(size):
    if size not in ("small", "medium", "large", "xl"):
        raise LibraryError("Invalid text size")
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences["text_size"] = size
    _write_json(PREFERENCES_PATH, preferences)


def font_family():
    preferences = _read_json(PREFERENCES_PATH, {})
    family = preferences.get('font_family', 'opendyslexic') if isinstance(preferences, dict) else 'opendyslexic'
    return family if family in ('opendyslexic', 'ubuntu', 'tinos') else 'opendyslexic'


def set_font_family(family):
    if family not in ('opendyslexic', 'ubuntu', 'tinos'):
        raise LibraryError('Invalid font')
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences['font_family'] = family
    _write_json(PREFERENCES_PATH, preferences)


def idle_light_minutes():
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        return 5
    value = preferences.get("idle_light_minutes")
    if type(value) is int and value in (0, 1, 5, 10):
        return value
    # Preserve the previous opt-out when upgrading the on/off setting.
    return 0 if preferences.get("idle_lights_enabled") is False else 5


def set_idle_light_minutes(minutes):
    if type(minutes) is not int or minutes not in (0, 1, 5, 10):
        raise LibraryError("Invalid light sleep time")
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences["idle_light_minutes"] = minutes
    _write_json(PREFERENCES_PATH, preferences)


def sleep_timer_minutes():
    preferences = _read_json(PREFERENCES_PATH, {})
    value = preferences.get('sleep_timer_minutes', 0) if isinstance(preferences, dict) else 0
    return value if value in (0, 5, 10, 30) else 0


def set_sleep_timer_minutes(minutes):
    if minutes not in (0, 5, 10, 30):
        raise LibraryError('Invalid sleep timer')
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences['sleep_timer_minutes'] = minutes
    _write_json(PREFERENCES_PATH, preferences)


def reading_light_mode():
    preferences = _read_json(PREFERENCES_PATH, {})
    mode = preferences.get("reading_light_mode", "off") if isinstance(preferences, dict) else "off"
    return mode if mode in ("off", "book", "chapter") else "off"


def set_reading_light_mode(mode):
    if mode not in ("off", "book", "chapter"):
        raise LibraryError("Invalid reading light mode")
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences["reading_light_mode"] = mode
    _write_json(PREFERENCES_PATH, preferences)


def quick_boot_enabled():
    preferences = _read_json(PREFERENCES_PATH, {})
    return isinstance(preferences, dict) and preferences.get("quick_boot") is True


def set_quick_boot_enabled(enabled):
    preferences = _read_json(PREFERENCES_PATH, {})
    if not isinstance(preferences, dict):
        preferences = {}
    preferences["quick_boot"] = bool(enabled)
    _write_json(PREFERENCES_PATH, preferences)


def version_for(entry, size):
    versions = entry.get("versions", {})
    version = versions.get(size)
    if not version:
        if size != "medium":
            raise LibraryError("Library needs new text-size versions")
        return entry
    selected = dict(entry)
    selected.update(version)
    return selected


def remove_download(entry):
    """Remove only the device copy; the catalog and progress remain intact."""
    path = package_path(entry)
    index = BOOK_DIR + '/' + _safe_id(entry['id']) + '.pri'
    if _exists(index):
        os.remove(index)
    if _exists(path):
        os.remove(path)
    for size in ("small", "medium", "large", "xl"):
        try:
            os.remove(path + "." + size + ".pages")
        except OSError:
            pass
    state = installed_state()
    if state.get(entry["id"], {}).get("local_only"):
        local = _read_json(LOCAL_CATALOG_PATH, {})
        local["books"] = [item for item in local.get("books", []) if item.get("id") != entry["id"]]
        _write_json(LOCAL_CATALOG_PATH, local)
    state.pop(entry["id"], None)
    _write_json(CACHE_STATE_PATH, state)


def _next_sequence():
    reading = _read_json(READING_STATE_PATH, {})
    sequence = int(reading.get("sequence", 0)) + 1
    reading["sequence"] = sequence
    _write_json(READING_STATE_PATH, reading)
    return sequence


def mark_read(book_id):
    book_id = _safe_id(book_id)
    reading = _read_json(READING_STATE_PATH, {})
    sequence = int(reading.get("sequence", 0)) + 1
    reading["sequence"] = sequence
    reading.setdefault("books", {})[book_id] = sequence
    _write_json(READING_STATE_PATH, reading)
    path = BOOK_DIR + "/" + book_id + ".prp"
    state = _read_book_progress(book_id)
    state["book_id"] = book_id
    state["last_read_order"] = sequence
    state.setdefault("bookmarks", [])
    _write_json(path, state)


def most_recent_read(entries):
    orders = _read_json(READING_STATE_PATH, {}).get("books", {})
    ranked = [(int(orders.get(entry.get("id"), 0)), index)
              for index, entry in enumerate(entries)]
    if not ranked:
        return None
    order, index = max(ranked)
    return index if order > 0 else None


def ensure_space(required, protected_id, recoverable=None):
    """Evict eligible downloads, oldest unread then least recently read."""
    def free_bytes():
        stats = os.statvfs(BOOK_DIR)
        return stats[0] * stats[3]
    if free_bytes() >= required:
        return
    state = installed_state()
    read_order = _read_json(READING_STATE_PATH, {}).get("books", {})
    candidates = []
    for book_id, metadata in state.items():
        if book_id == protected_id or not _exists(package_path({"id": book_id})):
            continue
        if recoverable is not None:
            # Only evict a copy that can be downloaded again byte-for-byte.
            expected = recoverable.get(book_id)
            if not expected or expected != metadata.get('sha256'):
                continue
            try:
                digest = hashlib.sha256()
                with open(package_path({'id': book_id}), 'rb') as source:
                    while True:
                        data = source.read(4096)
                        if not data:
                            break
                        digest.update(data)
                if binascii.hexlify(digest.digest()).decode() != expected:
                    continue
            except OSError:
                continue
        elif metadata.get("local_only"):
            continue
        order = read_order.get(book_id)
        candidates.append((0 if order is None else 1,
                           int(metadata.get("download_order", 0)) if order is None else int(order),
                           book_id))
    candidates.sort()
    available = free_bytes()
    if available < required:
        for _, _, book_id in candidates:
            available += os.stat(package_path({"id": book_id}))[6]
            if recoverable is not None:
                try:
                    available += os.stat(BOOK_DIR + '/' + book_id + '.pri')[6]
                except OSError:
                    pass
        if available < required:
            raise LibraryError("Not enough storage for this book")
    for _, _, book_id in candidates:
        if free_bytes() >= required:
            return
        remove_download({"id": book_id})
        if recoverable is not None:
            try:
                os.remove(BOOK_DIR + '/' + book_id + '.pri')
            except OSError:
                pass
    if free_bytes() < required:
        raise LibraryError("Not enough storage for this book")








def load_progress(book_ids=None):
    progress = _read_json(PROGRESS_PATH, {})
    if book_ids is not None:
        for book_id in book_ids:
            if not book_id:
                continue
            saved = _read_book_progress(book_id)
            if saved.get("book_id") == book_id:
                progress[book_id] = saved
        return progress
    try:
        files = os.listdir(BOOK_DIR)
    except OSError:
        return progress
    for name in files:
        if name.endswith(".prxl") or name.endswith(".prp"):
            book_id = name[:-5] if name.endswith(".prxl") else name[:-4]
            saved = _read_book_progress(book_id)
            if saved.get("book_id") == book_id:
                progress[book_id] = saved
    return progress



def _read_book_progress(book_id):
    book_id = _safe_id(book_id)
    path = BOOK_DIR + "/" + book_id + ".prp"
    if _exists(path):
        return _read_json(path, {})
    return _read_json(BOOK_DIR + "/" + book_id + ".prxl", {})


def save_progress(book_id, page, word):
    book_id = _safe_id(book_id)
    path = BOOK_DIR + "/" + book_id + ".prp"
    progress = _read_book_progress(book_id)
    progress.update({"book_id": book_id, "page": int(page), "word": int(word)})
    progress.setdefault("bookmarks", [])
    _write_json(path, progress)


def bookmarks(book_id):
    book_id = _safe_id(book_id)
    value = _read_book_progress(book_id)
    return sorted(set(int(word) for word in value.get("bookmarks", []) if int(word) >= 0))


def add_bookmark(book_id, word):
    book_id = _safe_id(book_id)
    path = BOOK_DIR + "/" + book_id + ".prp"
    state = _read_book_progress(book_id)
    state["book_id"] = book_id
    saved = sorted(set(int(item) for item in state.get("bookmarks", [])) | {int(word)})
    state["bookmarks"] = saved
    _write_json(path, state)


def remove_bookmarks(book_id, words):
    """Remove these bookmark positions without disturbing reading progress."""
    book_id = _safe_id(book_id)
    path = BOOK_DIR + "/" + book_id + ".prp"
    state = _read_book_progress(book_id)
    removed = set(int(word) for word in words)
    state["bookmarks"] = [int(word) for word in state.get("bookmarks", [])
                          if int(word) not in removed]
    _write_json(path, state)


class BookPackage:
    def __init__(self, entry):
        self.entry = entry
        self.handle = open(package_path(entry), "rb")
        self.header = json.loads(self.handle.readline())
        schema = self.header.get("schema")
        if type(schema) is int and schema > 5:
            self.handle.close()
            raise LibraryError("This book requires a newer reader. Please update your device.")
        if schema not in (1, 2, 3, 4, 5) or self.header.get("id") != entry.get("id"):
            self.handle.close()
            raise LibraryError("Book package metadata does not match catalog")
        self.data_offset = self.handle.tell()
        self.body_end = (self.data_offset + int(self.header["body_length"])
                         if "body_length" in self.header else None)
        self.pages = self.header.get("pages", [])
        self.size = None
        self.font_family = 'opendyslexic'
        self.advances = None
        self.lines_per_page = 0
        self._compact_notes = {}
        self._compact_page_details = {}

    def _load_compact_notes(self):
        length = int(self.header.get('notes_length', 0))
        if not length:
            self._compact_notes = {}
            return
        if self.body_end is None or length < 0 or length > 262144:
            raise LibraryError('Book footnotes are too large')
        self.handle.seek(self.body_end)
        raw = self.handle.read(length)
        if len(raw) != length:
            raise LibraryError('Book footnotes are incomplete')
        notes = json.loads(raw.decode('utf-8'))
        if not isinstance(notes, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in notes.items()):
            raise LibraryError('Book footnotes are invalid')
        self._compact_notes = notes

    def _lines(self, position=None):
        markup = self.header.get('schema') == 4
        larger = self._larger_metrics(self.size, self.font_family) if markup else None
        return pagination.layout_lines(self.handle, self.data_offset, self.advances,
                                       position, self.body_end, heading_advances=larger, markup=markup)

    def _layout_version(self):
        if self.header.get('schema') == 5:
            from lib import pocketreader_markup
            return pocketreader_markup.LAYOUT_VERSION
        return pagination.MARKUP_LAYOUT_VERSION if self.header.get('schema') == 4 else pagination.PAGINATION_VERSION

    def _compact_font_functions(self, size, family):
        sizes = ('small', 'medium', 'large', 'xl')
        spacing = (24, 32, 40, 56)
        base = sizes.index(size)
        def target(style):
            heading = style.get('heading')
            step = 2 if heading == 'h1' else 1 if heading == 'h2' else 0
            return min(3, base + step)
        def metrics(style):
            return self._font_metrics(sizes[target(style)], family)[0]
        def span(style):
            return (spacing[target(style)] + spacing[base] - 1) // spacing[base]
        return metrics, span

    def _legacy_font_index_safe(self):
        cached = getattr(self, '_legacy_font_safe', None)
        if cached is not None:
            return cached
        tail = b''
        try:
            with open(package_path(self.entry), 'rb') as source:
                while True:
                    chunk = source.read(4096)
                    if not chunk:
                        break
                    chunk = tail + chunk
                    if any(byte >= 128 for byte in chunk):
                        self._legacy_font_safe = False
                        return False
                    tail = chunk[-3:]
        except OSError:
            return False
        self._legacy_font_safe = True
        return True

    def _font_metrics_match(self, expected, advances):
        current = binascii.hexlify(hashlib.sha256(bytes(advances)).digest()).decode()
        if expected == current:
            return True
        old = binascii.hexlify(hashlib.sha256(bytes(advances[:95])).digest()).decode()
        return expected == old and self._legacy_font_index_safe()

    def _legacy_compact_metrics_hash(self, size, family):
        normal = self._font_metrics(size, family)[0]
        metrics, _ = self._compact_font_functions(size, family)
        combined = bytes(normal[:95]) + bytes(metrics({'heading':'h1'})[:95]) + bytes(metrics({'heading':'h2'})[:95])
        return binascii.hexlify(hashlib.sha256(combined).digest()).decode()

    def _compact_metrics_hash(self, size, family):
        normal = self._font_metrics(size, family)[0]
        metrics, _ = self._compact_font_functions(size, family)
        combined = bytes(normal) + bytes(metrics({'heading':'h1'})) + bytes(metrics({'heading':'h2'}))
        return binascii.hexlify(hashlib.sha256(combined).digest()).decode()

    def _larger_metrics(self, size, family):
        larger = {'small':'medium', 'medium':'large', 'large':'xl', 'xl':'xl'}[size]
        return self._font_metrics(larger, family)[0]

    def _larger_metrics_for_key(self, key):
        if ':' in key:
            family, size = key.split(':', 1)
        else:
            family, size = 'opendyslexic', key
        return self._larger_metrics(size, family)

    def _index_path(self):
        return BOOK_DIR + "/" + _safe_id(self.entry["id"]) + ".pri"

    def chapters(self):
        index = _read_json(self._index_path(), {})
        if (not isinstance(index, dict) or index.get("version") != 1
                or index.get("book_id") != self.entry.get("id")
                or index.get("sha256") != self.entry.get("sha256")):
            return []
        chapters = index.get("chapters", [])
        if not isinstance(chapters, list) or len(chapters) > 512:
            return []
        previous = -1
        total = int(self.header.get("word_count", 0))
        for chapter in chapters:
            if (not isinstance(chapter, list) or len(chapter) != 2
                    or type(chapter[0]) is not int or chapter[0] < previous
                    or chapter[0] < 0 or chapter[0] >= total
                    or not isinstance(chapter[1], str) or not chapter[1] or len(chapter[1]) > 120):
                return []
            previous = chapter[0]
        return chapters

    def _read_size_index(self, size, advances, lines):
        path = self._index_path()
        if not _exists(path) and _exists(path + '.index-backup'):
            os.rename(path + '.index-backup', path)
        index = _read_json(path, {})
        if (index.get("version") != 1 or index.get("book_id") != self.entry["id"]
                or index.get("sha256") != self.entry.get("sha256")):
            return None
        sizes = index.get("sizes", {})
        if not isinstance(sizes, dict):
            return None
        selected = sizes.get(size, {})
        if not isinstance(selected, dict):
            return None
        if (selected.get("layout_version") != self._layout_version() or not self._font_metrics_match(selected.get("metrics_sha256"), advances)
                or selected.get("lines") != lines):
            return None
        if self.header.get('schema') == 4:
            larger_hash = binascii.hexlify(hashlib.sha256(bytes(self._larger_metrics_for_key(size))).digest()).decode()
            if selected.get('heading_metrics_sha256') != larger_hash and not self._font_metrics_match(selected.get('heading_metrics_sha256'), self._larger_metrics_for_key(size)):
                return None
        if self.header.get('schema') == 5:
            family, label = size.split(':', 1) if ':' in size else ('opendyslexic', size)
            if selected.get('style_metrics_sha256') != self._compact_metrics_hash(label, family):
                if selected.get('style_metrics_sha256') != self._legacy_compact_metrics_hash(label, family) or not self._legacy_font_index_safe():
                    return None
        pages = selected.get("pages")
        if selected.get("encoding") == "delta-varint-base64":
            try:
                pages = pagination.unpack_page_positions(pages)
            except (ValueError, TypeError):
                return None
        if not isinstance(pages, list) or not pages:
            return None
        body_limit = ((self.body_end - self.data_offset) if self.body_end is not None
                      else os.stat(package_path(self.entry))[6] - self.data_offset)
        previous_offset, previous_word = -1, -1
        for page in pages:
            if not isinstance(page, list) or len(page) != 3:
                return None
            if any(not isinstance(value, int) or value < 0 for value in page):
                return None
            if page[0] >= body_limit or page[0] < previous_offset or page[2] < previous_word:
                return None
            previous_offset, previous_word = page[0], page[2]
        if self.header.get('schema') == 5:
            details = selected.get('note_pages', {})
            if not isinstance(details, dict) or (self.header.get('notes_length') and not details):
                return None
            for key, detail in details.items():
                if not isinstance(key, str) or not key.isdigit() or int(key) >= len(pages) or not isinstance(detail, dict):
                    return None
                if not isinstance(detail.get('body'), list) or not isinstance(detail.get('notes'), list):
                    return None
            self._compact_page_details = details
        # PRI positions are relative to the book body so the converter and
        # badge use the same portable format; the reader seeks absolute offsets.
        return [[self.data_offset + page[0], page[1], page[2]] for page in pages]

    def _save_size_index(self, size, advances, lines, pages):
        path = self._index_path()
        index = _read_json(path, {})
        if (index.get("version") != 1 or index.get("book_id") != self.entry["id"]
                or index.get("sha256") != self.entry.get("sha256")):
            index = {"version": 1, "book_id": self.entry["id"],
                     "sha256": self.entry.get("sha256"), "sizes": {}}
        index.setdefault("sizes", {})[size] = {
            "layout_version": self._layout_version(), "lines": lines,
            "metrics_sha256": binascii.hexlify(hashlib.sha256(bytes(advances)).digest()).decode(),
            "encoding": "delta-varint-base64",
            "pages": pagination.pack_page_positions(([page[0] - self.data_offset, page[1], page[2]] for page in pages))}
        if self.header.get('schema') == 4:
            index['sizes'][size]['heading_metrics_sha256'] = binascii.hexlify(
                hashlib.sha256(bytes(self._larger_metrics_for_key(size))).digest()).decode()
        if self.header.get('schema') == 5:
            family, label = size.split(':', 1) if ':' in size else ('opendyslexic', size)
            index['sizes'][size]['style_metrics_sha256'] = self._compact_metrics_hash(label, family)
            if self._compact_page_details:
                index['sizes'][size]['note_pages'] = self._compact_page_details
        _write_font_indexes(path, index, size)

    def _font_metrics(self, size, family):
        suffix = {'small': '18', 'medium': '24', 'large': '30', 'xl': '42'}[size]
        if family == 'ubuntu' and size == 'small':
            suffix = '20'
        prefix = {'opendyslexic': 'OPEN_DYSLEXIC', 'ubuntu': 'UBUNTU', 'tinos': 'TINOS'}[family]
        lines = {'small': 32, 'medium': 24, 'large': 19, 'xl': 14}[size]
        return getattr(font_data, prefix + suffix + '_ADVANCES'), lines

    async def prepare(self, size, family='opendyslexic'):
        if self.header["schema"] == 1:
            return
        self.size = size
        self.font_family = family
        self.advances, self.lines_per_page = self._font_metrics(size, family)
        if self.header.get('schema') == 5:
            self._load_compact_notes()
        key = size if family == 'opendyslexic' else family + ':' + size
        saved = self._read_size_index(key, self.advances, self.lines_per_page)
        if saved:
            self.pages = saved
            return
        embedded = self._embedded_index(key, self.advances, self.lines_per_page)
        if embedded is not None:
            self.pages = embedded
            self._save_size_index(key, self.advances, self.lines_per_page, embedded)
            return
        if self.header.get('schema') == 5:
            from lib import pocketreader_markup
            metrics, span = self._compact_font_functions(size, family)
            pages = PagePositions()
            try:
                self._compact_page_details = {}
                if self._compact_notes:
                    iterator = pocketreader_markup.iter_footnoted_preview(
                        self.handle, self.data_offset, self.advances, 608, self.lines_per_page,
                        self._compact_notes, metrics, span, self.body_end)
                else:
                    iterator = pocketreader_markup.iter_compact_pages(
                        self.handle, self.data_offset, self.advances, 608, self.lines_per_page,
                        metrics, with_positions=True, row_span_for_style=span, body_end=self.body_end)
                last_cursor = None
                for page in iterator:
                    if self._compact_notes:
                        body = page['body']
                        if body:
                            cursor = body[0][:3]
                            last_cursor = body[-1][:3]
                        elif last_cursor:
                            cursor = last_cursor
                        else:
                            raise LibraryError('Footnote continuation has no source page')
                        detail = pocketreader_markup.footnote_page_descriptor(page, self.data_offset)
                        if detail:
                            self._compact_page_details[str(len(pages))] = detail
                    else:
                        cursor = page[0][:3]
                    pages.append(list(cursor))
                    if len(pages) % 32 == 0:
                        await aio.sleep_ms(0)
                if not pages:
                    raise LibraryError('Book has no readable text')
                self.pages = pages
                self._save_size_index(key, self.advances, self.lines_per_page, pages)
            except MemoryError:
                pages.clear()
                self.pages = []
                self._compact_page_details = {}
                gc.collect()
                raise
            return
        pages = PagePositions()
        try:
            count = 0
            for offset, line_index, word, text in self._lines():
                if text is None:
                    count = 0
                    continue
                if pagination.needs_heading_page_break(text, count, self.lines_per_page,
                        self.header.get('schema') == 4 and self._larger_metrics(size, family) != self.advances):
                    count = 0
                if count == 0:
                    if not text:
                        continue
                    pages.append([offset, line_index, word])
                count += 1
                if count >= self.lines_per_page:
                    count = 0
                if len(pages) % 32 == 0:
                    await aio.sleep_ms(0)
            if not pages:
                raise LibraryError("Book has no readable text")
            self.pages = pages
            self._save_size_index(key, self.advances, self.lines_per_page, pages)
        except MemoryError:
            pages.clear()
            self.pages = []
            gc.collect()
            raise

    def has_index(self, size, family='opendyslexic'):
        if self.header.get("schema") == 1:
            return True
        advances, count = self._font_metrics(size, family)
        key = size if family == 'opendyslexic' else family + ':' + size
        if self._embedded_metadata(key, advances, count):
            return True
        return bool(self._read_size_index(key, advances, count))

    def _embedded_metadata(self, size, advances, lines):
        schema = self.header.get("schema")
        if schema not in (3, 4, 5) or self.header.get("index_version") != self._layout_version():
            return None
        meta = self.header.get("indexes", {}).get(size, {})
        if not self._font_metrics_match(meta.get("metrics_sha256"), advances) or meta.get("lines") != lines:
            return None
        if schema == 4:
            larger_hash = binascii.hexlify(hashlib.sha256(bytes(self._larger_metrics_for_key(size))).digest()).decode()
            if meta.get('heading_metrics_sha256') != larger_hash and not self._font_metrics_match(meta.get('heading_metrics_sha256'), self._larger_metrics_for_key(size)):
                return None
        if schema == 5:
            family, label = size.split(':', 1) if ':' in size else ('opendyslexic', size)
            if meta.get('style_metrics_sha256') != self._compact_metrics_hash(label, family):
                if meta.get('style_metrics_sha256') != self._legacy_compact_metrics_hash(label, family) or not self._legacy_font_index_safe():
                    return None
        offset, count = int(meta.get("offset", -1)), int(meta.get("count", 0))
        if offset < 0 or count < 1 or count > 100000 or self.body_end is None:
            return None
        file_size = os.stat(package_path(self.entry))[6]
        if self.body_end + offset + count * 12 > file_size:
            return None
        return (offset, count)

    def _embedded_index(self, size, advances, lines):
        meta = self._embedded_metadata(size, advances, lines)
        if meta is None:
            return None
        offset, count = meta
        self.handle.seek(self.body_end + offset)
        pages = []
        for _ in range(count):
            record = self.handle.read(12)
            if len(record) != 12:
                raise LibraryError("Book page index is incomplete")
            position, line, word = struct.unpack("<III", record)
            if position >= self.body_end - self.data_offset:
                raise LibraryError("Book page index is invalid")
            pages.append([self.data_offset + position, line, word])
        return pages

    async def locate_excerpt(self, excerpt, previous_word):
        """Match a legacy page's text to its word position in a reflowable book."""
        if self.header.get("schema") not in (2, 3, 4) or not excerpt:
            return None
        needle = excerpt.strip()[:60]
        self.handle.seek(self.data_offset)
        word = 0
        best = None
        while True:
            if self.body_end is not None and self.handle.tell() >= self.body_end:
                return best
            record = self.handle.readline()
            if not record:
                return best
            text = record.decode("utf-8", "replace")
            if self.header.get('schema') == 4 and text[:2] in (pagination.CENTER, pagination.HEADING, pagination.LARGE):
                text = text[2:]
            found = text.find(needle)
            if found >= 0:
                candidate = word + len(text[:found].split())
                if best is None or abs(candidate - previous_word) < abs(best - previous_word):
                    best = candidate
            if text.strip() != "\x1e":
                word += len(text.split())
            if word % 1024 < 20:
                await aio.sleep_ms(0)

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None

    def _read_compact_footnote_page(self, detail):
        from lib import pocketreader_markup
        metrics, _ = self._compact_font_functions(self.size, self.font_family)
        body = []
        cache = {}
        for relative, line_number, reserved in detail['body']:
            if reserved:
                body.append([('', {'_reserved': True})])
                continue
            offset = self.data_offset + int(relative)
            if offset not in cache:
                cache[offset] = {}
                for position, index, runs in pocketreader_markup.iter_layout_records(
                        self.handle, offset, self.advances, 608, metrics, body_end=self.body_end):
                    if position != offset:
                        break
                    cache[offset][index] = runs
            if line_number not in cache[offset]:
                raise LibraryError('Footnote page index is invalid')
            body.append(cache[offset][line_number])
        footer = []
        wrapped = {}
        for ident, line_number in detail['notes']:
            if ident not in self._compact_notes:
                raise LibraryError('Book footnote is missing')
            if ident not in wrapped:
                wrapped[ident] = pocketreader_markup.wrap_runs([(self._compact_notes[ident], {})], self.advances, 608)
            if not 0 <= int(line_number) < len(wrapped[ident]):
                raise LibraryError('Footnote page index is invalid')
            footer.append(wrapped[ident][int(line_number)])
        if detail.get('notice'):
            footer.append([('Footnote continues on next page', {})])
        if not detail.get('continuation'):
            body.extend([[] for _ in range(max(0, self.lines_per_page - len(body) - len(footer)))])
        return body + footer

    def read_page(self, page):
        page = max(0, min(int(page), len(self.pages) - 1))
        offset, length, start_word = self.pages[page]
        if self.header['schema'] == 5:
            from lib import pocketreader_markup
            detail = self._compact_page_details.get(str(page))
            if detail:
                return self._read_compact_footnote_page(detail), start_word
            metrics, span = self._compact_font_functions(self.size, self.font_family)
            pages = pocketreader_markup.iter_compact_pages(
                self.handle, offset, self.advances, 608, self.lines_per_page, metrics,
                row_span_for_style=span, skip_initial_lines=length, body_end=self.body_end)
            try:
                compact_page = next(pages)
            except StopIteration:
                compact_page = []
            return [item[-1] for item in compact_page], start_word
        if self.header["schema"] in (2, 3, 4):
            lines = []
            for record_offset, line_index, _, text in self._lines(offset):
                if record_offset == offset and line_index < length:
                    continue
                if text is None:
                    break
                if pagination.needs_heading_page_break(text, len(lines), self.lines_per_page,
                        self.header.get('schema') == 4 and self._larger_metrics(self.size, self.font_family) != self.advances):
                    break
                lines.append(text)
                if len(lines) >= self.lines_per_page:
                    break
            return lines, start_word
        self.handle.seek(self.data_offset + offset)
        text = self.handle.read(length).decode("utf-8", "replace")
        return text.split("\n"), start_word
