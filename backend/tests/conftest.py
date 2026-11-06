"""Test collection shim.

The DSH adapters import ``gateway.Relay``, which subclasses the Linux-only
``socketserver.ThreadingUnixStreamServer``.  On Windows the class definition
survives if a stand-in base exists; the pure decision-logic tests never
instantiate the relay.  On Linux this is a no-op and the real class is used.
"""
import socketserver

if not hasattr(socketserver, 'ThreadingUnixStreamServer'):
    socketserver.ThreadingUnixStreamServer = type('ThreadingUnixStreamServer', (), {})
