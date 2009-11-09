#!/usr/bin/env python

"""Communicating sequential processes, in Python.

When using CSP Python as a DSL, this module will normally be imported
via the statement 'from csp.cspprocess import *'. For that reason this
module imports all names from the csp.builtins module (and therefore
that module should not normally be imported by client code).

Copyright (C) Sarah Mount, 2009.

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A ParTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have rceeived a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA
"""

from __future__ import with_statement

__author__ = 'Sarah Mount <s.mount@wlv.ac.uk>'
__date__ = 'December 2008'

#DEBUG = True
DEBUG = False


def _debug(*args):
    """Customised debug logging.

    FIXME: Replace this with the builtin logging module.
    """
    smap = map(str, args)
    if DEBUG:
        print 'DEBUG:', ' '.join(smap)

from functools import wraps # Easy decorators

import copy
import inspect
import operator
import os
import random
import socket
import sys
import tempfile
import time
import uuid

# Are we sending secure messages?
try:
    import hmac
    import hashlib
    SECURITY_ON = True
except ImportError:
    SECURITY_ON = False
# Override the above, for testing:
SECURITY_ON = False

try: # Python optimisation compiler
    import psyco
    psyco.full()
except ImportError:
    print 'No available optimisation'

# Multiprocessing libary -- name changed between versions.
try:
    # Version 2.6 and above
    import multiprocessing as processing
    if sys.platform == 'win32':
        import processing.reduction
except ImportError:
    raise ImportError('No library available for multiprocessing.\n'+
                      'csp.cspprocess is only compatible with Python 2. 6 and above.')

try:
    import cPickle as mypickle # Faster pickle
except ImportError:
    import pickle as mypickle

### CONSTANTS

_BUFFSIZE = 1024

_HOST = socket.gethostbyname(socket.gethostname())
_CHANNEL_PORT = 9890
_OTA_PORT = 8888
_VIS_PORT = 8889

### Seeded random number generator (16 bytes)

_RANGEN = random.Random(os.urandom(16))

### Authentication

def _make_digest(message):
    """Return a digest for a given message."""
    return hmac.new('these/are/the/droids', message, hashlib.sha1).hexdigest()


class CorruptedData(Exception):
    """Used to verify that data has come from an honest source.
    """

    def __init__(self):
        super(CorruptedData, self).__init__()
        return

    def __str__(self):
        return 'Data sent with incorrect authentication key.'


class NoGuardInAlt(Exception):
    """Raised when an Alt has no guards to select.
    """

    def __init__(self):
        super(NoGuardInAlt, self).__init__()
        return

    def __str__(self):
        return 'Every Alt must have at least one guard.'


### Special constants / exceptions for termination and mobility
### Better not to use classes/objects here or pickle will get confused
### by the way that csp.__init__ manages the namespace.

_POISON = ';;;__POISON__;;;'
"""Used as special data sent down a channel to invoke termination."""

_SUSPEND = ';;;__SUSPEND__;;;' 	### NOT IMPLEMENTED
"""Used as special data sent down a channel to invoke suspension."""

_RESUME = ';;;__RESUME__;;;'	### NOT IMPLEMENTED
"""Used as special data sent down a channel to resume operation."""


class ChannelPoison(Exception):
    """Used to poison a processes and propagate to all known channels.
    """

    def __init__(self):
        super(ChannelPoison, self).__init__()
        return

    def __str__(self):
        return 'Posioned channel exception.'


class ChannelAbort(Exception):
    """Used to stop a channel write if a select aborts...
    """

    def __init__(self):
        super(ChannelAbort, self).__init__()
        return

    def __str__(self):
        return 'Channel abort exception.'


class ProcessSuspend(Exception):
    """Used to suspend a process.
    """

    def __init__(self):
        super(ProcessSuspend, self).__init__()
        return

    def __str__(self):
        return 'Process suspend exception.'

### Fundamental CSP concepts -- Processes, Channels, Guards

class CSPOpMixin(object):
    """Mixin class used for operator overloading in CSP process types.
    """

    def __init__(self):
        return

    def start(self):
        """Start only if self is not running."""
        if not self._popen:
            processing.Process.start(self)

    def join(self, timeout=None):
        """Join only if self is running and impose a timeout."""
        if self._popen:
            processing.Process.join(self, timeout)

    def referent_visitor(self, referents):
#        print type(referents), 'is the type of the referents',
#        try:
#            print len(referents), 'members'
#        except:
#            pass
        for obj in referents:
#            print type(obj)
            if obj is self or obj is None:
                continue
            if isinstance(obj, Channel):
#                print 'Poisoning', str(obj)
                obj.poison()
            elif ((hasattr(obj, '__getitem__') or hasattr(obj, '__iter__')) and
                  not isinstance(obj, basestring)):
#                print 'recursing over %s with %i members' %  (type(obj), len(obj))
                self.referent_visitor(obj)
            elif isinstance(obj, CSPProcess):
                self.referent_visitor(obj.args + tuple(obj.kwargs.values()))
            elif hasattr(obj, '__dict__'):
                self.referent_visitor(obj.__dict__.values())
		return

    def terminate(self):
        """Terminate only if self is running."""
        if self._popen:
            processing.Process.terminate(self)

    def __and__(self, other):
        """Implementation of CSP Par.

        Requires timeout with a small value to ensure
        parallelism. Otherwise a long sequence of '&' operators will
        run in sequence (because of left-to-right evaluation and
        orders of precedence).
        """
        assert _is_csp_type(other)
        par = Par(other, self, timeout = 0.1)
        par.start()
        return par

    def __gt__(self, other):
        """Implementation of CSP Seq."""
        assert _is_csp_type(other)
        seq = Seq(self, other)
        seq.start()
        return seq

    def __mul__(self, n):
        assert n > 0
        clone = None
        for i in xrange(n):
            clone = copy.copy(self)
            clone.start()
            clone.join()
            clone.terminate()
        return

    def __rmul__(self, n):
        assert n > 0
        clone = None
        for i in xrange(n):
            clone = copy.copy(self)
            clone.start()
            clone.join()
            clone.terminate()
        return


class CSPProcess(processing.Process, CSPOpMixin):
    """Implementation of CSP processes.
    Not intended to be used in client code. Use @process instead.
    """

    def __init__(self, func, *args, **kwargs):
        processing.Process.__init__(self,
                                    target=func,
                                    args=(args),
                                    kwargs=kwargs)        
        assert inspect.isfunction(func) # Check we aren't using objects
        assert not inspect.ismethod(func) # Check we aren't using objects
#        print inspect.ismethod(func)
#        print type(func)

        CSPOpMixin.__init__(self)
        for arg in list(self._args) + self._kwargs.values():
            if _is_csp_type(arg):
                arg.enclosing = self
        # Add a ref to this process so _target can access the
        # underlying operating system process.
        self._kwargs['_process'] = self
        self.enclosing = None
        return

    def getName(self):
        return self._name

    def getPid(self):
        return self._parent_pid
    
    def __str__(self):
        return 'CSPProcess running in PID %s' % self.getPid()

    def run(self): #, event=None):
        """Called automatically when the L{start} methods is called.
        """
        try:
            self._target(*self._args, **self._kwargs)
        except ChannelPoison:
            print str(self), 'in', self.getPid(), 'got ChannelPoison exception'
            self.referent_visitor(self.args + tuple(self.kwargs.values()))
#            for obj in self.args + tuple(self.kwargs.values()):
#                print type(obj)
            self.terminate()
        except ProcessSuspend:
            raise NotImplementedError('Process suspension not yet implemented')
        except Exception:
            typ, excn, tback = sys.exc_info()
            sys.excepthook(typ, excn, tback)
        return


class Guard(object):
    """Abstract class to represent CSP guards.

    All methods must be overridden in subclasses.
    """

    def is_selectable(self):
        """Should return C{True} if this guard can be selected by an L{Alt}.
        """
        raise NotImplementedError('Must be implemented in subclass')

    def enable(self):
        """Prepare for, but do not commit to a synchronisation.
        """
        raise NotImplementedError('Must be implemented in subclass')

    def disable(self):
        """Roll back from an L{enable} call.
        """
        raise NotImplementedError('Must be implemented in subclass')

    def select(self):
        """Commit to a synchronisation started by L{enable}.
        """
        raise NotImplementedError('Must be implemented in subclass')

    def poison(self):
        """Terminate all processes attached to this guard.
        """
        pass

    def __str__(self):
        return 'CSP Guard: must be subclassed.'

    def __or__(self, other):
        assert isinstance(other, Guard)
        return Alt(self, other).select()

    def __ror__(self, other):
        assert isinstance(other, Guard)
        return Alt(self, other).select()
    

class Channel(Guard):
    """CSP Channel objects.

    In python-csp there are two sorts of channel. In JCSP terms these
    are Any2Any, Alting channels. However, each channel creates an
    operating system level pipe. Since this is a file object the
    number of channels a program can create is limited to the maximum
    number of files the operating system allows to be open at any one
    time. To avoid this bottleneck use L{FileChannel} objects, which
    close the file descriptor used for IPC after every read or write
    operations. Read and write operations are, however, over 20 time
    slower when performed on L{FileChannel} objects.

    Subclasses of C{Channel} must call L{_setup()} in their
    constructor and override L{put}, L{get}, L{__del__},
    L{__getstate__} and L{__setstate__}, the latter two methods for
    pickling.
    """

    TRUE = 1
    FALSE = 0

    def __init__(self):
        self.name = uuid.uuid1()
        self._wlock = None       # Write lock protects from races between writers.
        self._rlock = None       # Read lock protects from races between readers.
        self._available = None     # Released if writer has made data available.
        self._taken = None         # Released if reader has taken data.
        self._is_alting = None     # True if engaged in an Alt synchronisation.
        self._is_selectable = None # True if can be selected by an Alt.
        self._has_selected = None  # True if already been committed to select.
        self._itemr, self._itemw = os.pipe()
        self._poisoned = None
        self._setup()
        super(Channel, self).__init__()
        return

    def _setup(self):
        """Set up synchronisation.

        MUST be called in __init__ of this class and all subclasses.
        """
        # Process-safe synchronisation.
        self._wlock = processing.RLock()    # Write lock.
        self._rlock = processing.RLock()    # Read lock.
        self._available = processing.Semaphore(0)
        self._taken = processing.Semaphore(0)
        # Process-safe synchronisation for CSP Select / Occam Alt.
        self._is_alting = processing.Value('h', Channel.FALSE)
        self._is_selectable = processing.Value('h', Channel.FALSE)
        # Kludge to say a select has finished (to prevent the channel
        # from being re-enabled). If values were really process safe
        # we could just have writers set _is_selectable and read that.
        self._has_selected = processing.Value('h', Channel.FALSE)
		# Is this channel poisoned?
        self._poisoned = processing.Value('h', Channel.FALSE)

    def __getstate__(self):
        """Return state required for pickling."""
        state = [self._available.get_value(),
                 self._taken.get_value(),
                 self._is_alting,
                 self._is_selectable,
                 self._has_selected]
        if self._available.get_value() > 0:
            obj = self.get()
        else:
            obj = None
        state.append(obj)
        return state

    def __setstate__(self, state):
        """Restore object state after unpickling."""
        self._wlock = processing.RLock()    # Write lock.
        self._rlock = processing.RLock()    # Read lock.
        self._itemr, self._itemw = os.pipe()
        self._available = processing.Semaphore(state[0])
        self._taken = processing.Semaphore(state[1])
        self._is_alting = processing.Value('h', state[2])
        self._is_selectable = processing.Value('h', state[3])
        self._has_selected = processing.Value('h', state[4])
        if state[5] is not None:
            self.put(state[5])
        return

    def put(self, item):
        """Put C{item} on a process-safe store.
        """
        os.write(self._itemw, mypickle.dumps(item, protocol=1))

    def get(self):
        """Get a Python object from a process-safe store.
        """
        data = []
        while True:
            sval = os.read(self._itemr, _BUFFSIZE)
            _debug('Read from OS pipe')
            if not sval:
                break
            data.append(sval)
#            _debug('Pipe got data: %i, %s' % (len(sval), sval))
            if len(sval) < _BUFFSIZE:
                break
        _debug('Left read loop')
        _debug('About to unmarshall this data:', ''.join(data)) 
        obj = None if data == [] else mypickle.loads(''.join(data))
        _debug('mypickle library has unmarshalled data.')
        return obj

    def __del__(self):
        os.close(self._itemr)
        os.close(self._itemw)
        return

    def is_selectable(self):
        """Test whether Alt can select this channel.
        """
        _debug('Alt THINKS _is_selectable IS: ' +
               str(self._is_selectable.value == Channel.TRUE))
        if self.is_poisoned(): raise ChannelPoison()

        return self._is_selectable.value == Channel.TRUE

    def write(self, obj):
        """Write a Python object to this channel.
        """
        if self.is_poisoned(): raise ChannelPoison()

        _debug('+++ Write on Channel %s started.' % self.name)
        with self._wlock: # Protect from races between multiple writers.
            # If this channel has already been selected by an Alt then
            # _has_selected will be True, blocking other readers. If a
            # new write is performed that flag needs to be reset for
            # the new write transaction.
            self._has_selected.value = Channel.FALSE
            # Make the object available to the reader.
            self.put(obj)
            self._available.release()
            _debug('++++ Writer on Channel %s: _available: %i _taken: %i. ' %
                   (self.name, self._available.get_value(),
                    self._taken.get_value()))
            # Block until the object has been read.
            self._taken.acquire()
            # Remove the object from the channel.
        _debug('+++ Write on Channel %s finished.' % self.name)
        return

    def read(self):
        """Read (and return) a Python object from this channel.
        """
        # FIXME: These assertions sometimes fail...why?
#        assert self._is_alting.value == Channel.FALSE
#        assert self._is_selectable.value == Channel.FALSE
        if self.is_poisoned(): raise ChannelPoison()

        _debug('+++ Read on Channel %s started.' % self.name)
        with self._rlock: # Protect from races between multiple readers.
            # Block until an item is in the Channel.
            _debug('++++ Reader on Channel %s: _available: %i _taken: %i. ' %
                   (self.name, self._available.get_value(),
                    self._taken.get_value()))
            self._available.acquire()
            # Get the item.
            _debug('++++ Reader on Channel %s: _available: %i _taken: %i. ' %
                   (self.name, self._available.get_value(),
                    self._taken.get_value()))
            obj = self.get()
            # Announce the item has been read.
            self._taken.release()
        _debug('+++ Read on Channel %s finished.' % self.name)
        if obj == _POISON:
            self.is_poisoned = True
            raise ChannelPoison()
        return obj

    def enable(self):
        """Enable a read for an Alt select.

        MUST be called before L{select()} or L{is_selectable()}.
        """
        if self.is_poisoned(): raise ChannelPoison()

        # Prevent re-synchronization.
        if (self._has_selected.value == Channel.TRUE or
            self._is_selectable.value == Channel.TRUE):
            return
        self._is_alting.value = Channel.TRUE
        with self._rlock:
            # Attempt to acquire _available.
            time.sleep(0.00001) # Won't work without this -- why?
            retval = self._available.acquire(block=False)
        if retval:
            self._is_selectable.value = Channel.TRUE
        else:
            self._is_selectable.value = Channel.FALSE
        _debug('Enable on guard', self.name, '_is_selectable:',
               self._is_selectable.value, '_available:',
               self._available.get_value())
        return

    def disable(self):
        """Disable this channel for Alt selection.

        MUST be called after L{enable} if this channel is not selected.
        """
        if self.is_poisoned(): raise ChannelPoison()

        self._is_alting.value = Channel.FALSE
        if self._is_selectable.value == Channel.TRUE:
            with self._rlock:
                self._available.release()
            self._is_selectable.value = Channel.FALSE
        return

    def select(self):
        """Complete a Channel read for an Alt select.
        """
        _debug('channel select starting')
        assert self._is_selectable.value == Channel.TRUE
        if self.is_poisoned(): raise ChannelPoison()

        with self._rlock:
            _debug('got read lock on channel',
                   self.name, '_available: ',
                   self._available.get_value())
            # Obtain object on Channel.
            obj = self.get()
            _debug('got obj')
            # Notify write() that object is taken.
            self._taken.release()
            _debug('released _taken')
            # Reset flags to ensure a future read / enable / select.
            self._is_selectable.value = Channel.FALSE
            self._is_alting.value = Channel.FALSE
            self._has_selected.value = Channel.TRUE
            _debug('reset bools')
        if obj == _POISON:
            self.poison()
            raise ChannelPoison()
        return obj

    def __str__(self):
        return 'Channel using OS pipe for IPC.'

    def is_poisoned(self):
        return self._poisoned.value == Channel.TRUE

    def poison(self):
        """Poison a channel causing all processes using it to terminate.
        """
        self._poisoned = processing.Value('h', Channel.TRUE)

    def suspend(self):
        """Suspend this mobile channel before migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')

    def resume(self):
        """Resume this mobile channel after migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')


class FileChannel(Channel):
    """Channel objects using files on disk.

    C{FileChannel} objects close their files after each read or write
    operation. The advantage of this is that client code can create as
    many C{FileChannel} objects as it wishes (unconstrained by the
    operating system's maximum number of open files). In return there
    is a performance hit -- reads and writes are around 10 x slower on
    C{FileChannel} objects compared to L{Channel} objects.
    """

    def __init__(self):
        self.name = Channel.NAMEFACTORY.name()
        self._wlock = None	# Write lock.
        self._rlock = None	# Read lock.
        self._available = None
        self._taken = None
        self._is_alting = None
        self._is_selectable = None
        self._has_selected = None
        # Process-safe store.
        file_d, self._fname = tempfile.mkstemp()
        os.close(file_d)
        self._setup()
        return

    def __getstate__(self):
        """Return state required for pickling."""
        state = [mypickle.dumps(self._available, protocol=1),
                 mypickle.dumps(self._taken, protocol=1),
                 mypickle.dumps(self._is_alting, protocol=1),
                 mypickle.dumps(self._is_selectable, protocol=1),
                 mypickle.dumps(self._has_selected, protocol=1),
                 self._fname]
        if self._available.get_value() > 0:
            obj = self.get()
        else:
            obj = None
        state.append(obj)
        return state

    def __setstate__(self, state):
        """Restore object state after unpickling."""
        self._wlock = processing.RLock()	# Write lock.
        self._rlock = processing.RLock()	# Read lock.
        self._available = mypickle.loads(state[0])
        self._taken = mypickle.loads(state[1])
        self._is_alting = mypickle.loads(state[2])
        self._is_selectable = mypickle.loads(state[3])
        self._has_selected = mypickle.loads(state[4])
        self._fname = state[5]
        if state[6] is not None:
            self.put(state[6])
        return

    def put(self, item):
        """Put C{item} on a process-safe store.
        """
        file_d = file(self._fname, 'w')
        file_d.write(mypickle.dumps(item, protocol=1))
        file_d.flush()
        file_d.close()
        return

    def get(self):
        """Get a Python object from a process-safe store.
        """
        stored = ''
        while stored == '':
            file_d = file(self._fname, 'r')
            stored = file_d.read()
            file_d.close()
        # Unlinking here ensures that FileChannel objects exhibit the
        # same semantics as Channel objects.
        os.unlink(self._fname)
        obj = mypickle.loads(stored)
        return obj

    def __del__(self):
        if os.path.exists(self._fname):
            # Necessary if the Channel has been deleted by poisoning.
            os.unlink(self._fname)
        return

    def __str__(self):
        return 'Channel using files for IPC.'

    def suspend(self):
        """Suspend this mobile channel before migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')

    def resume(self):
        """Suspend this mobile channel after migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')


class NetworkChannel(Channel):
    """Network channels ...
    """
    
    def __init__(self):
        self.name = Channel.NAMEFACTORY.name()
        self._wlock = None	# Write lock.
        self._rlock = None	# Read lock.
        self._available = None
        self._taken = None
        self._is_alting = None
        self._is_selectable = None
        self._has_selected = None
        # Process-safe store.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._setup()
        return

    def __getstate__(self):
        """Return state required for pickling."""
        state = [mypickle.dumps(self._available, protocol=1),
                 mypickle.dumps(self._taken, protocol=1),
                 mypickle.dumps(self._is_alting, protocol=1),
                 mypickle.dumps(self._is_selectable, protocol=1),
                 mypickle.dumps(self._has_selected, protocol=1),
                 self._fname]
        if self._available.get_value() > 0:
            obj = self.get()
        else:
            obj = None
        state.append(obj)
        return state

    def __setstate__(self, state):
        """Restore object state after unpickling."""
        self._wlock = processing.RLock()	# Write lock.
        self._rlock = processing.RLock()	# Read lock.
        self._available = mypickle.loads(state[0])
        self._taken = mypickle.loads(state[1])
        self._is_alting = mypickle.loads(state[2])
        self._is_selectable = mypickle.loads(state[3])
        self._has_selected = mypickle.loads(state[4])
        if state[5] is not None:
            self.put(state[5])
        return

    def put(self, item):
        """Put C{item} on a process-safe store.
        """
        self.sock.sendto(mypickle.dumps(item, protocol=1),
                         (_HOST, _CHANNEL_PORT))
        return

    def get(self):
        """Get a Python object from a process-safe store.
        """
        data = self.sock.recv(_BUFFSIZE)
        obj = mypickle.loads(data)
        return obj

    def __del__(self):
        self.sock.close()
        del self
        return
    
    def __str__(self):
        return 'Channel using sockets for IPC.'

    def suspend(self):
        """Suspend this mobile channel before migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')

    def resume(self):
        """Suspend this mobile channel after migrating between processes.
        """
        raise NotImplementedError('Suspend / resume not implemented')
    
### CSP combinators -- Par, Alt, Seq, ...

class Alt(CSPOpMixin):
    """CSP select (OCCAM ALT) process.

    What should happen if a guard is poisoned?
    """

    def __init__(self, *args):
        super(Alt, self).__init__()
        for arg in args:
            assert isinstance(arg, Guard)
        self.guards = list(args)
        self.last_selected = None

    def poison(self):
        """Poison the last selected guard and unlink from the guard list.

        Sets self.last_selected to None.
        """
        _debug(type(self.last_selected))
        self.last_selected.disable() # Just in case
        try:
            self.last_selected.poison()
        except:
            pass
        _debug('Poisoned last selected.')
        self.guards.remove(self.last_selected)
        _debug('%i guards' % len(self.guards))
        self.last_selected = None

    def _preselect(self):
        """Check for special cases when any form of select() is called.
        """
        if len(self.guards) == 0:
            raise NoGuardInAlt()
        elif len(self.guards) == 1:
            _debug('Alt Selecting unique guard:', self.guards[0].name)
            self.last_selected = self.guards[0]
            self.guards[0].enable()
            while not self.guards[0].is_selectable():
                time.sleep(0.01)
            return self.guards[0].select()
        return None

    def select(self):
        """Randomly select from ready guards."""
        if len(self.guards) < 2:
            return self._preselect()
        for guard in self.guards:
            guard.enable()
        _debug('Alt enabled all guards')
        ready = [guard for guard in self.guards if guard.is_selectable()]
        while len(ready) == 0:
            time.sleep(0.01) # Not sure about this.
            ready = [guard for guard in self.guards if guard.is_selectable()]
            _debug('Alt got no items to choose from')
        _debug('Alt got %i items to choose from' % len(ready))
        selected = _RANGEN.choice(ready)
        self.last_selected = selected
        for guard in self.guards:
            if guard is not selected:
                guard.disable()
        return selected.select()

    def fair_select(self):
        """Select a guard to synchronise with. Do not select the
        previously selected guard (unless it is the only guard
        available).
        """
        if len(self.guards) < 2:
            return self._preselect()
        for guard in self.guards:
            guard.enable()
        _debug('Alt enabled all guards')
        ready = [guard for guard in self.guards if guard.is_selectable()]
        while len(ready) == 0:
            time.sleep(0.1) # Not sure about this.
            ready = [guard for guard in self.guards if guard.is_selectable()]
        _debug('Alt got %i items to choose from' % len(ready))
        selected = None
        if self.last_selected in ready and len(ready) > 1:
            ready.remove(self.last_selected)
            _debug('Alt removed last selected from ready list')
        selected = _RANGEN.choice(ready)
        self.last_selected = selected
        for guard in self.guards:
            if guard is not selected:
                guard.disable()
        return selected.select()

    def pri_select(self):
        """Select a guard to synchronise with, in order of
        "priority". The guard with the lowest index in the L{guards}
        list has the highest priority.
        """
        if len(self.guards) < 2:
            return self._preselect()
        for guard in self.guards:
            guard.enable()
        _debug('Alt enabled all guards')
        ready = [guard for guard in self.guards if guard.is_selectable()]
        while len(ready) == 0:
            time.sleep(0.01) # Not sure about this.
            ready = [guard for guard in self.guards if guard.is_selectable()]
        _debug('Alt got %i items to choose from' % len(ready))
        self.last_selected = ready[0]
        for guard in ready[1:]:
            guard.disable()
        return ready[0].select()

    def __mul__(self, n):
        assert n > 0
        for i in xrange(n):
            yield self.select()
        return

    def __rmul__(self, n):
        assert n > 0
        for i in xrange(n):
            yield self.select()
        return


class Par(processing.Process, CSPOpMixin):
    """Run CSP processes in parallel.
    """

    def __init__(self, *procs, **kwargs):
        super(Par, self).__init__(None)
        if 'timeout' in kwargs:
            self.timeout = kwargs['timeout']
        else:
            self.timeout = 0.5
        self.procs = []
        for proc in procs:
            # FIXME: only catches shallow nesting.
            if isinstance(proc, Par):
                self.procs += proc.procs
            else:
                self.procs.append(proc)
        for proc in self.procs:
            proc.enclosing = self
        _debug('# processes in Par:', len(self.procs))
        return

    def __str__(self):
        return 'CSP Par running in process %i.' % self.getPid()

    def run(self):
        """Run this process. Analogue of L{CSPProcess.run}.
        """
        self.start()

    def _terminate(self):
        """Terminate the execution of this process.
        """
        for proc in self.procs:
            proc.terminate()
        if self._popen:
            self.terminate()

    def start(self):
        """Start then synchronize with the execution of parallel processes.
        Return when all parallel processes have returned.
        """
        try:
            for proc in self.procs:
                proc.start()
            for proc in self.procs:
                proc.join(self.timeout)
        except ChannelPoison:
            print str(self), 'in', self.getPid(), 'got ChannelPoison exception'
            self.referent_visitor(self.args + tuple(self.kwargs.values()))
            self.terminate()
        except ProcessSuspend:
            raise NotImplementedError('Process suspension not yet implemented')
        except Exception:
            typ, excn, tback = sys.exc_info()
            sys.excepthook(typ, excn, tback)
        return


class Seq(processing.Process, CSPOpMixin):
    """Run CSP processes sequentially.
    """

    def __init__(self, *procs):
        super(Seq, self).__init__()
        self.procs = []
        for proc in procs:
            # FIXME: only catches shallow nesting.
            if isinstance(proc, Seq):
                self.procs += proc.procs
            else:
                self.procs.append(proc)
        for proc in self.procs:
            proc.enclosing = self
        return

    def __str__(self):
        return 'CSP Seq running in process %i.' % self.getPid()

    def stop(self):
        """Terminate the execution of this process.
        """
        for proc in self.procs:
            proc._terminate()
        if self._popen:
            self.terminate()

    def start(self):
        """Start this process running.
        """
        try:
            for proc in self.procs:
                proc.start()
                proc.join()
        except ChannelPoison:
            print str(self), 'in', self.getPid(), 'got ChannelPoison exception'
            self.referent_visitor(self.args + tuple(self.kwargs.values()))
            self.terminate()
        except ProcessSuspend:
            raise NotImplementedError('Process suspension not yet implemented')
        except Exception:
            typ, excn, tback = sys.exc_info()
            sys.excepthook(typ, excn, tback)
        return

### Function decorators

def process(func):
    """Decorator to turn a function into a CSP process.

    Note that the function itself will not be a CSPProcess object, but
    will generate a CSPProcess object when called.
    """

    @wraps(func)
    def _call(*args, **kwargs):
        """Call the target function."""
        return CSPProcess(func, *args, **kwargs)
    return _call


### List of CSP based types (class names). Used by _is_csp_type.
_CSPTYPES = [CSPProcess, Par, Seq, Alt]


def _is_csp_type(name):
    """Return True if name is any type of CSP process."""
    for typ in _CSPTYPES:
        if isinstance(name, typ):
            return True
    return False

# Design patterns

class TokenRing(Par):
    def __init__(self, func, size, numtoks=1, _process=None):
        self.chans = [Channel() for channel in xrange(size)]
        self.procs = [func(index=i,
                           tokens=numtoks,
                           numnodes=size,
                           inchan=self.chans[i-1],
                           outchan=self.chans[i]) for i in xrange(size)]
        super(TokenRing, self).__init__(*self.procs) 
        return

# PlugNPlay guards and processes

class Skip(Guard):
    """Guard which will always return C{True}. Useful in L{Alt}s where
    the programmer wants to ensure that L{Alt.select} will always
    synchronise with at least one guard.
    """

    def __init__(self):
        Guard.__init__(self)

    def is_selectable(self):
        """Skip is always selectable."""
        return True

    def enable(self):
        """Has no effect."""
        return

    def disable(self):
        """Has no effect."""
        return

    def select(self):
        """Has no effect."""
        return 'Skip'

    def __str__(self):
        return 'Skip guard is always selectable.'


class TimerGuard(Guard):
    """Guard which only commits to synchronisation when a timer has expired.
    """

    def __init__(self):
        super(TimerGuard, self).__init__()
        self.now = time.time()
        self.name = 'Timer guard created at:' + str(self.now)
        self.alarm = None
        return

    def set_alarm(self, timeout):
        self.now = time.time()
        self.alarm = self.now + timeout
        return
    
    def is_selectable(self):
        self.now = time.time()
        if self.alarm is None:
            return True
        elif self.now < self.alarm:
            return False
        return True

    def read(self):
        """Return current time.
        """
        self.now = time.time()
        return self.now

    def sleep(self, timeout):
        """Put this process to sleep for a number of seconds.
        """
        time.sleep(timeout)
        return
    
    def enable(self):
        return
    
    def disable(self):
        return

    def select(self):
        return

@process
def Zeroes(cout, _process=None):
    """Writes out a stream of zeroes."""
    while True:
        cout.write(0)
    return


@process
def Id(cin, cout, _process=None):
    """Id is the CSP equivalent of lambda x: x.
    """
    while True:
        cout.write(cin.read())
    return


@process
def Succ(cin, cout, _process=None):
    """Succ is the successor process, which writes out 1 + its input event.
    """
    while True:
        cout.write(cin.read() + 1)
    return


@process
def Pred(cin, cout, _process=None):
    """Pred is the predecessor process, which writes out 1 - its input event.
    """
    while True:
        cout.write(cin.read() - 1)
    return


@process
def Prefix(cin, cout, prefix_item=None, _process=None):
    """Prefix a write on L{cout} with the value read from L{cin}.

    @type prefix_item: object
    @param prefix_item: prefix value to use before first item read from L{cin}.
    """
    pre = prefix_item
    while True:
        cout.write(pre)
        pre = cin.read()
    return


@process
def Delta2(cin, cout1, cout2, _process=None):
    """Delta2 sends input values down two output channels.
    """
    while True:
        val = cin.read()
        cout1.write(val)
        cout2.write(val)
    return


@process
def Mux2(cin1, cin2, cout, _process=None):
    """Mux2 provides a fair multiplex between two input channels.
    """
    alt = Alt(cin1.read, cin2.read)
    while True:
        guard = alt.pri_select()
        cout.write(guard.read())
    return

@process
def Multiply(cin0,cin1,cout0,_process=None):
    
    while True:
        cout0.write(cin0.read() * cin1.read())
    return


@process
def Clock(cout, resolution=1, _process=None):
    """Send None object down output channel every C{resolution} seconds.
    """
    while True:
        time.sleep(resolution)
        cout.write(None)
    return


@process
def Printer(cin, _process=None, out=sys.stdout):
    """Print all values read from L{cin} to standard out or L{out}.
    """
    while True:
        msg = str(cin.read()) + '\n'
        out.write(msg)
    return


@process
def Pairs(cin1, cin2, cout, _process=None):
    """Read values from L{cin1} and L{cin2} and write their addition
    to L{cout}.
    """
    while True:
        in1 = cin1.read()
        in2 = cin2.read()
        cout.write(in1 + in2)
    return


@process
def Mult(cin, cout, scale, _process=None):
    """Scale values read on L{cin} and write to L{cout}.
    """
    while True:
        cout.write(cin.read() * scale)
    return


@process
def Generate(cout, _process=None):
    """Generate successive (+ve) ints and write to L{cout}.
    """
    counter = 0
    while True:
        cout.write(counter)
        counter += 1
    return


@process
def FixedDelay(cin, cout, delay, _process=None):
    """Read values from L{cin} and write to L{cout} after a delay of
    L{delay} seconds.
    """
    while True:
        in1 = cin.read()
        time.sleep(delay)
        cout.write(in1)
    return


@process
def Fibonacci(cout, _process=None):
    """Write successive Fibonacci numbers to L{cout}.
    """
    a_int = b_int = 1
    while True:
        cout.write(a_int)
        a_int, b_int = b_int, a_int + b_int
    return


@process
def Blackhole(cin, _process=None):
    """Read values from L{cin} and do nothing with them.
    """
    while True:
        cin.read()
    return


@process
def Sign(cin, cout, prefix, _process=None):
    """Read values from L{cin} and write to L{cout}, prefixed by L{prefix}.
    """
    while True:
        val = cin.read()
        cout.write(prefix + str(val))
    return


### Magic for processes built on Python operators

def _applyunop(unaryop, docstring):
    """Create a process whose output is C{unaryop(cin.read())}.
    """

    @process
    def _myproc(cin, cout, _process=None):
        while True:
            in1 = cin.read()
            cout.write(unaryop(in1))
        return
    _myproc.__doc__ = docstring
    return _myproc


def _applybinop(binop, docstring):
    """Create a process whose output is C{binop(cin1.read(), cin2.read())}.
    """

    @process
    def _myproc(cin1, cin2, cout, _process=None):
        while True:
            in1 = cin1.read()
            in2 = cin2.read()
            cout.write(binop(in1, in2))
        return
    _myproc.__doc__ = docstring
    return _myproc

# Numeric operators

Plus = _applybinop(operator.__add__,
                   """Writes out the addition of two input events.
""")

Sub = _applybinop(operator.__sub__,
                  """Writes out the subtraction of two input events.
""")

Mul = _applybinop(operator.__mul__,
                  """Writes out the multiplication of two input events.
""")

Div = _applybinop(operator.__div__,
                  """Writes out the division of two input events.
""")


FloorDiv = _applybinop(operator.__floordiv__,
                       """Writes out the floor div of two input events.
""")

Mod = _applybinop(operator.__mod__,
                  """Writes out the modulo of two input events.
""")

Pow = _applybinop(operator.__pow__,
                  """Writes out the power of two input events.
""")

LShift = _applybinop(operator.__lshift__,
                     """Writes out the left shift of two input events.
""")

RShift = _applybinop(operator.__rshift__,
                     """Writes out the right shift of two input events.
""")

Neg = _applyunop(operator.__neg__,
                 """Writes out the negation of input events.
""")

# Bitwise operators

Not = _applyunop(operator.__inv__,
                 """Writes out the inverse of input events.
""")

And = _applybinop(operator.__and__,
                  """Writes out the bitwise and of two input events.
""")

Or = _applybinop(operator.__or__,
                 """Writes out the bitwise or of two input events.
""")

Nand = _applybinop(lambda x, y: ~ (x & y),
                   """Writes out the bitwise nand of two input events.
""")

Nor = _applybinop(lambda x, y: ~ (x | y),
                  """Writes out the bitwise nor of two input events.
""")

Xor = _applybinop(operator.xor,
                  """Writes out the bitwise xor of two input events.
""")

# Logical operators

Land = _applybinop(lambda x, y: x and y,
                   """Writes out the logical and of two input events.
""")

Lor = _applybinop(lambda x, y: x or y,
                  """Writes out the logical or of two input events.
""")

Lnot = _applyunop(operator.__not__,
                  """Writes out the logical not of input events.
""")

Lnand = _applybinop(lambda x, y: not (x and y),
                    """Writes out the logical nand of two input events.
""")

Lnor = _applybinop(lambda x, y: not (x or y),
                   """Writes out the logical nor of two input events.
""")

Lxor = _applybinop(lambda x, y: (x or y) and (not x and y),
                   """Writes out the logical xor of two input events.
""")

# Comparison operators

Eq = _applybinop(operator.__eq__,
                 """Writes True if two input events are equal (==).
""")

Ne = _applybinop(operator.__ne__,
                   """Writes True if two input events are not equal (not ==).
""")

Geq = _applybinop(operator.__ge__,
                   """Writes True if 'right' input event is >= 'left'.
""")

Leq = _applybinop(operator.__le__,
                   """Writes True if 'right' input event is <= 'left'.
""")

Gt = _applybinop(operator.__gt__,
                   """Writes True if 'right' input event is > 'left'.
""")

Lt = _applybinop(operator.__lt__,
                   """Writes True if 'right' input event is < 'left'.
""")

Is = _applybinop(lambda x, y: x is y,
                 """Writes True if two input events are the same (is).
""")

Is_Not = _applybinop(lambda x, y: not (x is y),
                   """Writes True if two input events are not the same (is).
""")
