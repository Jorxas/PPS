"""Read-only Windows memory telemetry; commit is distinct from physical RAM."""
import ctypes
import os
import psutil

GIB = 1024 ** 3


def memory_snapshot():
    mem = psutil.virtual_memory()
    result = {"physical_total_gib": mem.total / GIB, "physical_available_gib": mem.available / GIB}
    if os.name == "nt":
        class PerformanceInfo(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
                ("CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable",
                 "SystemCache", "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")] + [
                (name, ctypes.c_ulong) for name in ("HandleCount", "ProcessCount", "ThreadCount")]
        info = PerformanceInfo()
        info.cb = ctypes.sizeof(info)
        api = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
        api.argtypes = [ctypes.POINTER(PerformanceInfo), ctypes.c_ulong]
        api.restype = ctypes.c_int
        if not api(ctypes.byref(info), info.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        for name in ("CommitTotal", "CommitLimit", "CommitPeak", "PhysicalAvailable", "SystemCache"):
            result[name + "_gib"] = getattr(info, name) * info.PageSize / GIB
        result["commit_headroom_gib"] = (info.CommitLimit - info.CommitTotal) * info.PageSize / GIB
    own = psutil.Process().memory_info()
    result["process"] = {"pid": os.getpid(), "working_set_bytes": own.rss,
                         "private_committed_bytes": getattr(own, "private", None),
                         "peak_working_set_bytes": getattr(own, "peak_wset", None),
                         "psutil_vms_bytes": own.vms}
    return result
