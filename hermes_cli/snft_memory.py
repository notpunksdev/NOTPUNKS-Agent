"""Best-effort local memory hardening for protected sNFT capsules."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
import platform
import resource
from typing import Any


_HARDENED = False


@dataclass
class LockedSecret:
    """Mutable secret buffer with best-effort lock/dontdump and zeroize support."""

    buffer: bytearray
    locked: bool = False
    dontdump: bool = False

    def zeroize(self) -> None:
        for index in range(len(self.buffer)):
            self.buffer[index] = 0


def harden_current_process() -> dict[str, Any]:
    """Disable easy process dumps/debugging where the host OS allows it.

    This is defensive hardening, not a TEE. A user who controls the machine can
    still attack the process with enough privileges.
    """
    global _HARDENED
    result: dict[str, Any] = {
        "core_dumps_disabled": False,
        "dumpable_disabled": False,
        "platform": platform.system().lower(),
    }
    if _HARDENED:
        result["already_hardened"] = True
        return result

    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result["core_dumps_disabled"] = True
    except Exception as exc:
        result["core_dump_error"] = str(exc)

    if platform.system().lower() == "linux":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            pr_set_dumpable = 4
            if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) == 0:
                result["dumpable_disabled"] = True
            else:
                result["dumpable_error"] = os.strerror(ctypes.get_errno())
        except Exception as exc:
            result["dumpable_error"] = str(exc)

    _HARDENED = True
    return result


def _buffer_address(buffer: bytearray) -> tuple[int, int]:
    if not buffer:
        return 0, 0
    c_buffer = (ctypes.c_char * len(buffer)).from_buffer(buffer)
    return ctypes.addressof(c_buffer), len(buffer)


def lock_secret(data: bytes | bytearray) -> LockedSecret:
    """Copy data into a mutable buffer and apply mlock/dontdump when possible."""
    buffer = bytearray(data)
    secret = LockedSecret(buffer=buffer)
    if not buffer:
        return secret

    address, size = _buffer_address(buffer)
    if not address or not size:
        return secret

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except Exception:
        return secret

    try:
        if libc.mlock(ctypes.c_void_p(address), ctypes.c_size_t(size)) == 0:
            secret.locked = True
    except Exception:
        pass

    if platform.system().lower() == "linux":
        try:
            madv_dontdump = 16
            if libc.madvise(ctypes.c_void_p(address), ctypes.c_size_t(size), ctypes.c_int(madv_dontdump)) == 0:
                secret.dontdump = True
        except Exception:
            pass

    return secret


def unlock_secret(secret: LockedSecret) -> None:
    if not secret.locked or not secret.buffer:
        return
    try:
        address, size = _buffer_address(secret.buffer)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.munlock(ctypes.c_void_p(address), ctypes.c_size_t(size))
    except Exception:
        pass
    finally:
        secret.locked = False
