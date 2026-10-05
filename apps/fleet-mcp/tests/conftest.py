"""No test may reach the network: a live API call from a test would spend real money.

pytest loads this file by itself. `python -m unittest discover tests` doesn't, so
test_network_guard.py imports it: with either runner the guard is on before the first test runs.

A connection or DNS lookup for any host but this machine raises LiveNetworkCall. It's a
BaseException on purpose: the apps catch every Exception from the network and keep going (a paid
run must never crash), and that would quietly swallow a test's live call instead of failing it."""

import ipaddress
import socket


class LiveNetworkCall(BaseException):
    """A test tried to reach the network. Give it a fake client or transport instead."""


def is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    host = ("" if host is None else str(host)).strip("[]").split("%")[0].lower()
    if host in ("", "localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


_connect, _connect_ex, _getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo


def _refuse(host, port) -> None:
    raise LiveNetworkCall(f"a test tried to reach {host}:{port}; tests must use fakes, never the network")


def guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(address[0]):
        _refuse(*address[:2])
    return _connect(self, address)


def guarded_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not is_local(address[0]):
        _refuse(*address[:2])
    return _connect_ex(self, address)


def guarded_getaddrinfo(host, port, *args, **kwargs):
    if not is_local(host):
        _refuse(host, port)
    return _getaddrinfo(host, port, *args, **kwargs)


socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
socket.getaddrinfo = guarded_getaddrinfo
