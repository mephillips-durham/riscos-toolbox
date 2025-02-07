"""RISC OS Toolbox library: events"""

from collections.abc import Iterable
from functools import wraps
import ctypes
import inspect
import swi

from ._consts import Wimp
from ._types import BBox, Point


# Handlers
# --------
# The following is based on the toolbox event handlers. A similar mechanism
# is used for both wimp messages and wimp events.
#
# To handle a toolbox event, the @toolbox_handler decorator is used on a member of
# a class derived from riscos_toolbox.  event.EvebtDispatcher, such as .Object
# or .Application. The decorator can be used to match all components (with
# @ToolboxEvent(event)...), one component (@toolbox_handler(event, component)...)
# or a list of components (@toolbox_handler(event, [comp1,comp2]...)
#
# 'event' can either be a class name derived from Event, or an integer number. In
# the first case, the handler will be called with an instance of the class, created
# using it's from_block method. In the second, the handler will be called with the
# raw data from the wimp poll block.
#
# When a toolbox event is received, the library will try each of the self,
# parent and ancestor objects, followed by the application object, to see if
# it has a suitable handler.
#
# For each level; if a a handler exists for the component id (from id_block.
# self.component) it will be called, or if no such handler exists, but there
# is an "all components" handler that will  be called.
#
# This means a "all components" handler from a more specific object will
# take precedence over a more specific one from a less specific object.
#
# If one is found and it DOESN'T return False, the process with end. If the
# handler is not found, or returns  False, the next one will be tried. Not
# returning anything from the handler will therefore cause further handlers not
# to be tried.
#
# handlers
# { event :
#     { class-name :
#         { component | None:
#             (handler-function, data-class | None )
#         }
#     }
# }
#
# Wimp message reply handlers
# ---------------------------
# Wimp messages have an extra 'send' function which can be used to send them
# to another task. This optionally has a callback function to call when a reply
# is recieved (a message where the 'your ref' of the a message to matches the
# one sent). On a reply the callback function will be called with the message.
# If the callback function  doesn't return False, then no further processing will
# take place on the message. If it DOES return False, it will be offered to the
# handlers in the usual way. If no reply is recieved the callback will be called
# with None. In either case, the callback will be removed from the list of callbacks.


class Event(object):
    event_id = None

    @classmethod
    def from_poll_block(cls, poll_block):
        """Create an object setup from `poll_block` (a byte string). The default
           version here will setup a ctypes Structure derived class."""
        if issubclass(cls, ctypes.Structure):
            if len(poll_block) < ctypes.sizeof(cls):
                raise RuntimeError(
                    "not enough data for {}".format(cls.__name__))
            obj = cls()
            dst = ctypes.cast(
                ctypes.pointer(obj), ctypes.POINTER(ctypes.c_byte))
            for b in range(0, ctypes.sizeof(cls)):
                dst[b] = poll_block[b]
            return obj

        else:
            raise RuntimeError(
                "from_block not implemented for {}".format(cls.__name__))


class ToolboxEvent(Event, ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("reference_number", ctypes.c_int32),
        ("event_code", ctypes.c_uint32),
        ("flags", ctypes.c_uint32)
    ]


class AboutToBeShownEvent(ToolboxEvent):
    ShowType_Default = 0
    ShowType_FullSpec = 1
    ShowType_TopLeft = 2
    ShowType_Centre = 3
    ShowType_AtPointer = 4

    _fields_ = [
        ("show_type", ctypes.c_uint32),
        ("_visible_area", BBox),
        ("_scroll", Point),
        ("_behind", ctypes.c_int32),
        ("_window_flags", ctypes.c_uint32),
        ("+parent_window_handle", ctypes.c_int32),
        ("_alignment_flags", ctypes.c_uint32)]

    def get_if(self, value, show_type):
        return value if self.show_type == show_type else None

    @property
    def top_left(self):
        return self.get_if(self._visible_area.min,
                           AboutToBeShownEvent.ShowType_TopLeft)

    @property
    def visible_area(self):
        return self.get_if(self._visible_area,
                           AboutToBeShownEvent.ShowType_FullSpec)

    @property
    def scroll(self):
        return self.get_if(self._scroll,
                           AboutToBeShownEvent.ShowType_FullSpec)

    @property
    def behind(self):
        return self.get_if(self._behind,
                           AboutToBeShownEvent.ShowType_FullSpec)

    @property
    def window_flags(self):
        return self.get_if(self._window_flags,
                           AboutToBeShownEvent.ShowType_FullSpec)

    @property
    def parent_window_handle(self):
        return self.get_if(self._parent_window_handle,
                           AboutToBeShownEvent.ShowType_FullSpec)

    @property
    def alignment_flags(self):
        return self.get_if(self._alignment_flags,
                           AboutToBeShownEvent.ShowType_FullSpec)


class UserMessage(Event, ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("sender", ctypes.c_int32),
        ("my_ref", ctypes.c_uint32),
        ("your_ref", ctypes.c_uint32),
        ("code", ctypes.c_uint32),
    ]

    # Message sending functions.
    # if reply_callback is not None, it will be called with a reply
    # or None if no reply is recieved. The reply callback takes two parameters:
    # the message info and the message data. See the @reply_handler decorator.
    def broadcast(self, recorded=False, size=None,
                  reply_callback=None):
        """Sends the message as a broadcast."""
        self.your_ref = 0
        self._send(Wimp.UserMessageRecorded if recorded else Wimp.UserMessage,
                   None, None, size, reply_callback)

    def send(self, task=None, window=None, iconbar=None,
             recorded=False, size=None,
             reply_callback=None):
        """Sendds the message to a task, window or iconbar icon."""
        self.your_ref = 0
        if task:
            handle, icon = task, 0
        elif window:
            handle, icon = window, 0
        elif iconbar:
            handle, icon = -2, iconbar
        else:
            handle, icon = 0, 0  # Broadcast

        self._send(Wimp.UserMessageRecorded if recorded else Wimp.UserMessage,
                   handle, icon, reply_callback)

    def reply(self, reply, recorded=False, size=None,
              reply_callback=None, reply_messages=None):
        """Reply to this message with the one given in reply"""
        reply.your_ref = self.my_ref
        reply._send(Wimp.UserMessageRecorded if recorded else Wimp.UserMessage,
                    self.sender, None, size, reply_callback)

    def acknowledge(self):
        self.your_ref = self.my_ref
        return swi.swi('Wimp_SendMessage', 'IIiI;..i',
                       Wimp.UserMessageAcknowledge,
                       ctypes.addressof(self),
                       self.sender, 0)

    def _send(self, reason, target, icon, size, reply_callback):
        self.size = size or ctypes.sizeof(self)
        self.code = self.__class__.event_id
        handle = swi.swi('Wimp_SendMessage', 'IIii;..i',
                         reason, ctypes.addressof(self),
                         target or 0, icon or 0)
        if reply_callback:
            _reply_callbacks[self.my_ref] = reply_callback

        return handle if target != 0 else None


# Contains info about a message - the data from the header, plus the wimp
# reason it was delivered with. If used like an 'int' will give the message
# code.
class MessageInfo(int):
    def create(reason, size, sender, my_ref, your_ref, code):
        mc = MessageInfo(code)
        mc.reason = reason
        mc.size = size
        mc.sender = sender
        mc.my_ref = my_ref
        mc.your_ref = your_ref
        mc.code = code
        return mc

    @property
    def recorded(self):
        return self.reason == Wimp.UserMessageRecorded

    @property
    def bounce(self):
        return self.reason == Wimp.UserMessageAcknowledge


class EventHandler(object):
    """Base class for things that can handle events."""

    def __init__(self):
        # event: component: [(handler, data-class)..]
        self.toolbox_handlers = {}
        self.wimp_handlers = {}
        self.message_handlers = {}

        def _build_handlers(registry, handlers, classname):
            for event, handler_map in registry.items():
                if classname in handler_map:
                    for component, handler in handler_map[classname].items():
                        if event not in handlers:
                            handlers[event] = {component: [handler]}
                        elif component not in handlers[event]:
                            handlers[event][component] = [handler]
                        else:
                            handlers[event][component].append(handler)

        for klass in inspect.getmro(self.__class__):
            classname = klass.__qualname__

            _build_handlers(_toolbox_handlers,
                            self.toolbox_handlers, classname)
            _build_handlers(_wimp_handlers,
                            self.wimp_handlers, classname)
            _build_handlers(_message_handlers,
                            self.message_handlers, classname)

    def _dispatch(self, handlers, event, id_block, poll_block):
        if event not in handlers:
            return False

        handlers = handlers[event]
        component = id_block.self.component

        def _data(data_class, poll_block):
            if data_class is not None:
                return data_class.from_poll_block(poll_block)
            return poll_block

        if component in handlers:
            for handler, data_class in handlers[component]:
                if handler(self, event, id_block, _data(data_class, poll_block)) is not False:
                    return True

        if None in handlers:
            for handler, data_class in handlers[None]:
                if handler(self, event, id_block, _data(data_class, poll_block)) is not False:
                    return True

    def toolbox_dispatch(self, event, id_block, poll_block):
        return self._dispatch(self.toolbox_handlers,
                              event, id_block, poll_block)

    def wimp_dispatch(self, reason, id_block, poll_block):
        return self._dispatch(self.wimp_handlers,
                              reason, id_block, poll_block)

    def message_dispatch(self, code, id_block, poll_block):
        return self._dispatch(self.message_handlers,
                              code, id_block, poll_block)


# Handlers
_toolbox_handlers = {}
_wimp_handlers = {}
_message_handlers = {}
_reply_messages = set()  # @reply_handler messages
_reply_callbacks = {}  # {ref: MessageReplyCallback}


def _set_handler(code, component, handler, handlers):
    if '.' in handler.__qualname__:
        cls = handler.__qualname__.rsplit('.', 1)[0]
    else:
        cls = None

    def _add_handler(handlers, code, component, cls, handler):
        if isinstance(code, int):
            event_type = None
        elif issubclass(code, Event):
            event_type = code
            code = code.event_id
        else:
            raise RuntimeError("Handler must be for int or Event")

        if code in handlers.keys():
            if cls in handlers[code]:
                handlers[code][cls][component] = handler
            else:
                handlers[code][cls] = {component: (handler, event_type)}
        else:
            handlers[code] = {cls: {component: (handler, event_type)}}

    if isinstance(code, Iterable):
        for code in code:
            _add_handler(handlers, code, component, cls, handler)
    else:
        _add_handler(handlers, code, component, cls, handler)

    return handler


def toolbox_handler(event, component=None):
    def decorator(handler):
        return _set_handler(event, component, handler, _toolbox_handlers)
    return decorator


def message_handler(message, component=None):
    def decorator(handler):
        return _set_handler(message, component, handler, _message_handlers)
    return decorator


def wimp_handler(reason, component=None):
    def decorator(handler):
        return _set_handler(reason, component, handler, _wimp_handlers)
    return decorator


def reply_handler(message_s):
    _message_map = {}  # message number -> class or None

    def _map_data(code_or_class):
        if isinstance(code_or_class, int):
            return code_or_class, None
        elif issubclass(code_or_class, UserMessage):
            return code_or_class.event_id, code_or_class
        else:
            raise RuntimeError("Must be int or UserMessage")

    if isinstance(message_s, Iterable):
        for m in message_s:
            code, klass = _map_data(m)
            _message_map[code] = klass
    else:
        code, klass = _map_data(message_s)
        _message_map[code] = klass

    _reply_messages.update(set(_message_map.keys()))

    def decorator(handler):
        @wraps((handler, _message_map))
        def wrapper(self, data, *args):
            message = None
            code = None
            if data is not None:
                message = ctypes.cast(
                    data, ctypes.POINTER(UserMessage)
                ).contents

                code = message.code
                if code in _message_map:
                    message = ctypes.cast(
                        data, ctypes.POINTER(_message_map[code])
                    ).contents
            return handler(self, code, message, *args)
        return wrapper
    return decorator


# List of self, parent, ancestor and application objects (if they exist)
# This is the list of objects to try to handle the event, in order.
def _get_spaa(application, id_block):
    from .base import get_object
    return list(
        filter(lambda o: o is not None,
               map(get_object,
                   set([
                       id_block.self.id,
                       id_block.parent.id,
                       id_block.ancestor.id,
                   ])
                   )
               )
    ) + ([application] if application else [])


def toolbox_dispatch(event_code, application, id_block, poll_block):
    for obj in _get_spaa(application, id_block):
        if obj.toolbox_dispatch(event_code, id_block, poll_block):
            break


def message_dispatch(code, application, id_block, poll_block):
    if code.your_ref in _reply_callbacks:
        r = _reply_callbacks[code.your_ref](poll_block)
        del _reply_callbacks[code.your_ref]
        if r is not False:
            return

    if code.reason == Wimp.UserMessageAcknowledge and code.my_ref in _reply_callbacks:
        r = _reply_callbacks[code.my_ref](poll_block)
        del _reply_callbacks[code.my_ref]
        if r is not False:
            return

    for obj in _get_spaa(application, id_block):
        if obj.message_dispatch(code, id_block, poll_block):
            break


def wimp_dispatch(reason, application, id_block, poll_block):
    for obj in _get_spaa(application, id_block):
        if obj.wimp_dispatch(reason, id_block, poll_block):
            break


def null_polls():
    return len(_reply_callbacks) > 0


def null_poll():
    for ref in list(_reply_callbacks.keys()):
        _reply_callbacks[ref](None)
        del _reply_callbacks[ref]


def registered_wimp_events():
    return _wimp_handlers.keys()
