import ctypes
import os
import sys
from ctypes import wintypes

PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DUPLICATE_CLOSE_SOURCE = 0x1
DUPLICATE_SAME_ACCESS = 0x2
FILE_TYPE_DISK = 0x1
PROCESS_HANDLE_INFORMATION = 51
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
ERROR_MORE_DATA = 234
CCH_RM_SESSION_KEY = 32
RM_MAX_APP_NAME = 255
RM_MAX_SVC_NAME = 63


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("Process", _UniqueProcess),
        ("strAppName", wintypes.WCHAR * (RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


class _HandleEntry(ctypes.Structure):
    _fields_ = [
        ("HandleValue", wintypes.HANDLE),
        ("HandleCount", ctypes.c_size_t),
        ("PointerCount", ctypes.c_size_t),
        ("GrantedAccess", wintypes.ULONG),
        ("ObjectTypeIndex", wintypes.ULONG),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


def _windows() -> bool:
    return sys.platform == "win32"


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    return kernel


def locking_processes(path: str) -> list[tuple[int, str]]:
    if not _windows():
        return []
    try:
        manager = ctypes.WinDLL("rstrtmgr")
    except OSError:
        return []
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    if manager.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(os.path.abspath(path))
        if manager.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        size = 16
        for _attempt in range(4):
            needed = wintypes.UINT()
            count = wintypes.UINT(size)
            infos = (_ProcessInfo * size)()
            reasons = wintypes.DWORD()
            result = manager.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if result == ERROR_MORE_DATA:
                size = needed.value + 4
                continue
            if result != 0:
                return []
            return [(infos[index].Process.dwProcessId, infos[index].strAppName) for index in range(count.value)]
        return []
    finally:
        manager.RmEndSession(session)


def _same_file(handle, kernel, target: str) -> bool:
    if kernel.GetFileType(handle) != FILE_TYPE_DISK:
        return False
    buffer = ctypes.create_unicode_buffer(1024)
    length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        return False
    name = buffer.value
    if name.startswith("\\\\?\\UNC\\"):
        name = "\\\\" + name[8:]
    elif name.startswith("\\\\?\\"):
        name = name[4:]
    return os.path.normcase(os.path.normpath(name)) == target


def _process_handles(process) -> list[int]:
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtQueryInformationProcess.restype = ctypes.c_ulong
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]
    size = 0x10000
    for _attempt in range(8):
        buffer = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG()
        status = ntdll.NtQueryInformationProcess(
            process, PROCESS_HANDLE_INFORMATION, ctypes.addressof(buffer), size, ctypes.byref(needed)
        )
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, needed.value + 0x1000)
            continue
        if status != 0:
            return []
        count = ctypes.c_size_t.from_buffer(buffer).value
        offset = ctypes.sizeof(ctypes.c_size_t) * 2
        stride = ctypes.sizeof(_HandleEntry)
        count = min(count, (size - offset) // stride)
        entries = (_HandleEntry * count).from_buffer(buffer, offset)
        return [entry.HandleValue for entry in entries if entry.HandleValue]
    return []


def _close_in_process(pid: int, target: str, kernel) -> int:
    process = kernel.OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, pid)
    if not process:
        process = kernel.OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return 0
    closed = 0
    try:
        current = kernel.GetCurrentProcess()
        for value in _process_handles(process):
            copy = wintypes.HANDLE()
            if not kernel.DuplicateHandle(process, value, current, ctypes.byref(copy), 0, False, DUPLICATE_SAME_ACCESS):
                continue
            try:
                matches = _same_file(copy, kernel, target)
            finally:
                kernel.CloseHandle(copy)
            if matches and kernel.DuplicateHandle(process, value, None, None, 0, False, DUPLICATE_CLOSE_SOURCE):
                closed += 1
    finally:
        kernel.CloseHandle(process)
    return closed


def release_file(path: str) -> list[str]:
    if not _windows():
        return []
    target = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    own = os.getpid()
    kernel = _kernel()
    released = []
    for pid, name in locking_processes(path):
        if pid == own:
            continue
        try:
            if _close_in_process(pid, target, kernel):
                released.append(name or f"process {pid}")
        except OSError:
            continue
    return released
