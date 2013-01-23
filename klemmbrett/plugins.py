#!/usr/bin/env python

import os as _os
import functools as _ft
import weakref as _weakref
import collections as _collections

import pygtk as _pygtk
_pygtk.require('2.0')
import gtk as _gtk
import gobject as _gobject
import keybinder as _keybinder

import klemmbrett as _klemmbrett
import klemmbrett.config as _config


class Plugin(_gobject.GObject):

    OPTIONS = {}

    def __init__(self, name, options, klemmbrett):
        super(Plugin, self).__init__()
        self.klemmbrett = _weakref.proxy(klemmbrett)
        self.options = options
        self.name = name

    def set(self, widget = None, text = None):
        #print "setting new content: %r" % (buf,)
        self.klemmbrett.set(text)

    def cleanup_text(self, text):
        return text.replace('\n', ' ')[
            :min(
                len(text),
                int(self.options.get('line-length', 30)),
            )
        ].strip()

    def bootstrap(self):
        pass


class StatusIcon(Plugin):

    def __init__(self, *args, **kwargs):
        super(StatusIcon, self).__init__(*args, **kwargs)

        self.menu = _gtk.Menu()
        item = _gtk.MenuItem("Quit")
        item.connect('activate', _gtk.main_quit)
        self.menu.append(item)
        self.menu.show_all()

        self.tray = _gtk.StatusIcon()
        self.tray.set_visible(True)
        self.tray.set_tooltip("Klemmbrett")
        self.tray.connect('popup-menu', self.on_menu, self.menu)

        icon = self.options.get('icon-path', None)
        if icon:
            self.tray.set_from_file(_os.path.expanduser(icon))
        else:
            self.tray.set_from_stock(_gtk.STOCK_ABOUT)

    def on_menu(self, icon, event_button, event_time, menu):
        menu.popup(
            None,
            None,
            _gtk.status_icon_position_menu,
            event_button,
            event_time,
            self.tray,
        )


class PopupPlugin(Plugin):

    def bootstrap(self):
        _keybinder.bind(
            self.options.get('shortcut', self._DEFAULT_BINDING),
            self.popup,
        )

    def popup(self):
        menu = _gtk.Menu()
        index = 0

        # XXX(mbra): this will not work with values as "no", "off" etc.
        # since we do not use getbool
        if self.options.get('show-current-selection', False) and len(self.history):
            item = _gtk.MenuItem("")
            item.get_children()[0].set_markup("<b>%s</b>" % (self.cleanup_text(self.history.top),))
            menu.append(item)
            menu.append(_gtk.SeparatorMenuItem())
            index += 1

        for pos, (label, value) in enumerate(self.items()):
            label = "_%s %s" % (pos, label)
            item = _gtk.MenuItem(label, use_underline = True)
            item.connect("activate", self.set, value)
            menu.append(item)

        menu.show_all()
        menu.popup(
            None,
            None,
            None,
            1,
            _gtk.get_current_event_time(),
        )
        menu.set_active(index)
        return True


class HistoryPicker(PopupPlugin):

    _DEFAULT_BINDING = "<Ctrl><Alt>C"

    __gsignals__ = {
        "text-accepted": (_gobject.SIGNAL_RUN_FIRST, None, (_gobject.TYPE_PYOBJECT,)),
    }

    OPTIONS = {
        "tie:history": "history",
    }

    def __init__(self, *args, **kwargs):
        super(HistoryPicker, self).__init__(*args, **kwargs)

        self._history = _collections.deque(
            maxlen = int(self.options.get("length", 15)),
        )

    def bootstrap(self):
        super(HistoryPicker, self).bootstrap()
        self.klemmbrett.connect("text-selected", self.add)

    def items(self):
        for text in self._history:
            yield (
                self.cleanup_text(text),
                text
            )

    def add(self, widget, text):
        if self.accepts(text):
            self._history.appendleft(text)
            self.emit("text-accepted", text)
            return True
        return False

    def __iter__(self):
        return iter(self._history)

    def __len__(self):
        return len(self._history)

    def accepts(self, text):
        # do not accept bullshit
        if not isinstance(text, basestring):
            return False

        # do not accept empty strings and pure whitespace strings
        text = text.strip()
        if not text:
            return False

        # accept everything if the history is empty
        if not len(self._history):
            return True

        # only if it is not the current selection
        return text != self.top

    @property
    def top(self):
        return self._history[0]


class SnippetPicker(PopupPlugin):

    _DEFAULT_BINDING = "<Ctrl><Alt>S"
    OPTIONS = {
        "tie:history": "history"
    }

    def items(self):
        if not self.klemmbrett.config.has_section('snippets'):
            return ValueError("No config section snippets defined")

        return self.klemmbrett.config.items('snippets')


class ActionPicker(PopupPlugin):

    _DEFAULT_BINDING = "<Ctrl><Alt>A"
    OPTIONS = {
        "tie:history": "history"
    }

    def items(self):
        if not self.klemmbrett.config.has_section('actions'):
            return ValueError("No config section actions defined")

        return self.klemmbrett.config.items('actions')

    def set(self, widget = None, text = None):
        try:
            command = "/bin/bash -c " + text % (self.history.top,)
            _gobject.spawn_async(["/bin/bash", "-c", text % (self.history.top,)])
        except StopIteration:
            pass