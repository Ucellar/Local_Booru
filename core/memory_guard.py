from __future__ import annotations

import os
import sys
import time
import gc


def _rss_psutil():
    try:
        import psutil  # type: ignore
        proc = psutil.Process(os.getpid())
        rss = int(proc.memory_info().rss)
        vm = psutil.virtual_memory()
        return {
            "rss_bytes": rss,
            "available_bytes": int(getattr(vm, "available", 0) or 0),
            "total_bytes": int(getattr(vm, "total", 0) or 0),
            "memory_load_percent": float(getattr(vm, "percent", 0.0) or 0.0),
        }
    except Exception:
        return None


def _rss_windows_ctypes():
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX),
        )
        if not ok:
            return None
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        return {
            "rss_bytes": int(counters.WorkingSetSize or counters.PrivateUsage or 0),
            "available_bytes": int(mem.ullAvailPhys or 0),
            "total_bytes": int(mem.ullTotalPhys or 0),
            "memory_load_percent": float(mem.dwMemoryLoad or 0),
        }
    except Exception:
        return None


def process_memory_snapshot():
    """Return process/system RAM snapshot.  All fields are best-effort bytes."""
    return _rss_psutil() or _rss_windows_ctypes() or {
        "rss_bytes": 0,
        "available_bytes": 0,
        "total_bytes": 0,
        "memory_load_percent": 0.0,
    }


def mb(value: int | float) -> float:
    return float(value or 0) / (1024.0 * 1024.0)


def format_snapshot(snap) -> str:
    snap = snap or {}
    rss = mb(int(snap.get("rss_bytes") or 0))
    avail = mb(int(snap.get("available_bytes") or 0))
    total = mb(int(snap.get("total_bytes") or 0))
    load = float(snap.get("memory_load_percent") or 0.0)
    return f"rss={rss:.0f} MB; free={avail:.0f}/{total:.0f} MB; load={load:.0f}%"


_LAST_TRIM = 0.0


def soft_trim_memory(min_interval_seconds: float = 10.0):
    """Best-effort Python/Qt memory trim.  Safe to call often; real work is throttled."""
    global _LAST_TRIM
    now = time.time()
    if now - _LAST_TRIM < float(min_interval_seconds or 10.0):
        return
    _LAST_TRIM = now
    try:
        gc.collect()
    except Exception:
        pass
    # Linux: return free arenas to the OS where supported.
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        pass

    # Windows: Task Manager's "Memory" column mostly reflects the working set.
    # After long parser runs Python/Pillow/Qt can free objects but Windows may keep
    # the process working set high until memory pressure.  Trim it explicitly so
    # STOP/DONE actually gives RAM back instead of looking like a leak.
    if sys.platform.startswith("win"):
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            try:
                ctypes.windll.psapi.EmptyWorkingSet(handle)
            except Exception:
                pass
            try:
                ctypes.windll.kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value)
            except Exception:
                pass
        except Exception:
            pass
