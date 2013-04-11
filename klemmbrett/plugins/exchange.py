#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os as _os
import sha as _sha
import hmac as _hmac
import time as _time
import functools as _ft
import socket as _socket
import xmlrpclib as _xmlrpc
import urlparse as _urlparse
import threading as _threading
import SimpleXMLRPCServer as _xmlrpcserver

import pygtk as _pygtk
_pygtk.require('2.0')
import gtk as _gtk
import gobject as _gobject
import keybinder as _keybinder

import pynotify as _notify
import Crypto.Cipher.AES as _aes

import klemmbrett.util as _util
import klemmbrett.plugins as _plugins


def hosttuple(host):
    parts = host.split(":")
    if len(parts) == 2:
        host, port = parts
    if len(parts) == 1:
        host, port = parts[0], 6789

    port = int(port)

    return (host, port)


class ClipboardExchangeHandler(_xmlrpcserver.SimpleXMLRPCRequestHandler):
    """
        The ClipboardExchangeHandler is used to inject the client_address
        into the method call of all exported xmlrpc methods, as well as
        decoding/verifing the xmlrpc encryption.
    """

    def __init__(self, destinations, *args, **kwargs):
        self._destinations = destinations
        _xmlrpcserver.SimpleXMLRPCRequestHandler.__init__(self, *args, **kwargs)

    def _dispatch(self, method, params):
        """ Inject the client_address into the argument list of all rpc methods """
        f = list(params)
        f.append(self.client_address)
        return self.server._dispatch(method, f)

    def decode_request_content(self, data):
        """ Do decryption/verification of the rpc request """
        try:
            hmac, issued, data = data.split("|")
            issued = int(issued)

            data = data.decode("hex")
            hmac = hmac.decode("hex")

            h = str(
                _hmac.new(
                    str(self._destinations[self.client_address[0]]["hmac-key"]),
                    msg = data,
                    digestmod = _sha,
                ).digest()
            )

            if h != hmac:
                self.send_response(400, "Request Verification failed")
                self.send_header("Content-length", "0")
                self.end_headers()
                return None


            # the first block_size bytes of the message is the initialization vector
            data = _aes.new(
                self._destinations[self.client_address[0]]["encryption-key"].decode("hex"),
                _aes.MODE_CFB,
                data[:_aes.block_size],
            ).decrypt(data[_aes.block_size:])

            # only accept messages in a narrow timeslot, best whould be
            # to have a logbook of request ids used or some sort of sequence
            # but that seems excessive at the moment, on the other hand this
            # requires the systems to sync their time, either by using a
            # timeserver or some sort of internal protocol
            now = _time.time()
            if issued < now - 5:
                self.send_response(400, "The request is no longer valid")
                self.send_header("Content-length", "0")
                self.end_headers()
                return None

            return _xmlrpcserver.SimpleXMLRPCRequestHandler.decode_request_content(self, data)
        except:
            import traceback as _tb
            _tb.print_exc()


class ClipboardExchangeTransport(_xmlrpc.Transport):
    """
        The ClipboardExchangeTransport is only used for encoding/signing
        the xmlrpc request
    """

    def __init__(self, encryption_key, hmac_key, use_datetime = 0):
        """ Store encryption and hmac key for later """
        _xmlrpc.Transport.__init__(self, use_datetime)
        self._encryption_key = encryption_key
        self._hmac_key = hmac_key

    def request(self, host, handler, request_body, verbose=0):
        """
            Intercept the request, hash it, encrypt it, add a timestampt
            and transmit it with the standard transports request method
        """
        encryption_iv = _os.urandom(_aes.block_size)
        issued = int(_time.time())

        request_body = encryption_iv + _aes.new(
            self._encryption_key.decode('hex'),
            _aes.MODE_CFB,
            encryption_iv,
        ).encrypt(request_body)
        h = _hmac.new(self._hmac_key, msg = request_body, digestmod = _sha).digest()

        return _xmlrpc.Transport.request(
            self,
            host,
            handler,
            '|'.join([
                h.encode('hex'),
                str(issued),
                request_body.encode('hex'),
            ]),
            verbose,
        )


class ClipboardExchange(_plugins.PopupPlugin):

    DEFAULT_BINDING = "<Ctrl><Alt>P"
    DEFAULT_ACCEPT_BINDING = "<Ctrl><Alt>X"
    DEFAULT_USERHISTORY_BINDING = "<Ctrl><Alt>D"
    OPTIONS = {
        "tie:history": "history",
    }

    def get_destinations(self):
        """
            Create a hash of all configured destinations, indexed
            by ip as a string, so we can look it up easily in the
            RequestHandler for injection into the argument list.

            Each hash contains the rpc url, the name of the user,
            a history object for that user as well as the encryption
            and hmac keys for this user, at the moment this keys
            are the same for all users, but this will change in
            the future.
        """
        dests = dict()

        for name, addr in self.options.iteritems():
            if not name.startswith('user.'):
                continue

            host, port = hosttuple(addr)
            ip = _socket.gethostbyname(host)
            url = "http://%s:%s" % (host, port)

            dests[ip] = {
                "name": name[5:],
                "url": url,
                "history": _plugins.HistoryController("history." + name[5:], self.options, self.klemmbrett),
                "encryption-key": self.options["encryption-key"],
                "hmac-key": self.options["hmac-key"],
            }

        return dests

    def bootstrap(self):
        """
            Setup all destinations and shortcuts and start the rpc server
        """
        self._destinations = self.get_destinations()

        # initialize standard stuff
        _notify.init("Klemmbrett")
        self._current_suggestion = None

        # binding to accept the suggested text into the clipboard
        _keybinder.bind(
            self.options.get('accept-suggestion-shortcut', self.DEFAULT_ACCEPT_BINDING),
            self._accept_suggestion,
        )

        _keybinder.bind(
            self.options.get('user-history-shortcut', self.DEFAULT_USERHISTORY_BINDING),
            self._show_histories,
        )

        _plugins.PopupPlugin.bootstrap(self)
        self._start_server()

    def _show_histories(self):
        """ Popup a dropdown menu with a submenu per user containing their history """
        return self.popup(((x["name"], x["history"].items) for x in self._destinations.values()))

    def _serve(self):
        """ Middleman for the Thread ctors target argument """
        return self._server.serve_forever()

    def _start_server(self):
        """
            Initialize the xmlrpc server, register all required functions
            and run the server in a seperate thread
        """
        host, port = hosttuple(self.options.get("listen", "0.0.0.0"))

        _gtk.gdk.threads_init()
        self._server = _xmlrpcserver.SimpleXMLRPCServer(
            (host, port),
            allow_none = True,
            requestHandler = _ft.partial(
                ClipboardExchangeHandler,
                self._destinations,
            ),
        )
        self._server.register_function(self._suggest, "suggest")
        #self._server.register_introspection_functions()

        t = _threading.Thread(target = self._serve)
        t.daemon = True
        t.start()

    def _accept_suggestion(self):
        """ Accept the current suggestion into the main clipboard history """
        self.history.add(self._current_history.top)
        self.klemmbrett.set(self._current_history.top)

    def _suggest(self, text, client_address):
        """ Display a message about the new suggestion and it origin """
        _gtk.gdk.threads_enter()
        n = _notify.Notification(
            "Suggestions from %r" % (
                self._destinations[client_address[0]]["name"],
            ),
            text,
        )
        self._destinations[client_address[0]]["history"].add(text)
        n.show()
        self._current_history = self._destinations[client_address[0]]["history"]
        _gtk.gdk.threads_leave()

    def items(self):
        """
            The default item provider for PopupPlugins. This will display
            the list of destinations to send the current clipboard contents to.
        """
        for dest in self._destinations.values():
            yield (
                dest["name"],
                _ft.partial(self._send_text, dest["url"], self.history.top),
            )

    def _send_text(self, dest, text):
        """
            Callback for the items method. sends a suggestion to the desired
            destination
        """
        p =  _xmlrpc.ServerProxy(
            dest,
            transport = ClipboardExchangeTransport(
                self.options["encryption-key"],
                self.options["hmac-key"],
            ),
        )
        p.suggest(text)