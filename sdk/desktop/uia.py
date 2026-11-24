"""Low-level UI Automation access shared by the SDK (`ui`) and the desktop recorder.

Wraps the few pywinauto/UIA primitives both sides need so that the selector a
recording stores is resolved at run time with exactly the same rules:

- `top_windows()` lists visible top-level windows (z order, process, class).
- `find_all(root, ...)` runs one native UIA FindAll over the descendants with
  Name / ControlType / AutomationId / ClassName conditions (fast even on big trees).
- `describe(element)` returns the plain dict a recording stores.

pywinauto is imported lazily: the SDK stays importable on machines without it
and only the hybrid `ui` actions require it.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Any

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Shell surfaces used to launch apps (taskbar, Start, search, desktop).
SHELL_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
    "Progman", "WorkerW", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland",
    "NotifyIconOverflowWindow",
})
SHELL_PROCESSES = frozenset({
    "explorer.exe", "searchhost.exe", "searchapp.exe", "searchui.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
})
# Control types whose Name/AutomationId rarely identify what was clicked.
WEAK_ROLES = frozenset({"Pane", "Window", "Custom", "Group", "Document", "Image", "Unknown"})


class UiaUnavailable(RuntimeError):
    pass


def _windows() -> bool:
    return sys.platform == "win32"


def dpi_aware() -> None:
    """Per-monitor v2 DPI awareness for the whole process (physical pixels everywhere).

    Must run before PyAutoGUI/pyscreeze import (they set a weaker mode); later
    calls fail harmlessly once the process mode is fixed.
    """
    if not _windows():
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except (AttributeError, OSError):
        pass


def load():
    """Import pywinauto's UIA layer (raises UiaUnavailable with an actionable message)."""
    if not _windows():
        raise UiaUnavailable("La automatización de interfaz (UIA) solo está disponible en Windows")
    dpi_aware()
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
    except ImportError as error:  # pragma: no cover - dependency missing
        raise UiaUnavailable("Instala pywinauto en el backend: pip install pywinauto") from error
    return IUIA, UIAElementInfo


# ----------------------------------------------------------------- windows


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process: str
    exe: str
    rect: tuple[int, int, int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd, "title": self.title, "class": self.class_name, "pid": self.pid,
            "process": self.process, "exe": self.exe, "rect": list(self.rect),
        }


def _user32():
    return ctypes.windll.user32


def _text(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(min(length, 1000) + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_pid(hwnd: int) -> int:
    return _pid(hwnd)


def _pid(hwnd: int) -> int:
    from ctypes import wintypes

    pid = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


_EXE_CACHE: dict[int, str] = {}


def exe_of(pid: int) -> str:
    if pid in _EXE_CACHE:
        return _EXE_CACHE[pid]
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    path = ""
    handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = buffer.value
        finally:
            kernel.CloseHandle(handle)
    if len(_EXE_CACHE) > 512:
        _EXE_CACHE.clear()
    _EXE_CACHE[pid] = path
    return path


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    from ctypes import wintypes

    rect = wintypes.RECT()
    # DWM extended frame bounds exclude the invisible resize borders of Win10/11.
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        _user32().GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def window_info(hwnd: int) -> WindowInfo:
    pid = _pid(hwnd)
    exe = exe_of(pid)
    return WindowInfo(
        hwnd=int(hwnd), title=_text(hwnd), class_name=_class(hwnd), pid=pid,
        process=os.path.basename(exe) if exe else "", exe=exe, rect=window_rect(hwnd),
    )


def root_window(hwnd: int) -> int:
    GA_ROOT = 2
    return int(_user32().GetAncestor(hwnd, GA_ROOT) or hwnd)


def window_at(x: int, y: int) -> int:
    from ctypes import wintypes

    hwnd = _user32().WindowFromPoint(wintypes.POINT(x, y))
    return root_window(hwnd) if hwnd else 0


def foreground() -> int:
    return int(_user32().GetForegroundWindow() or 0)


def _cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    status = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(value), 4)
    return status == 0 and bool(value.value)


def top_windows() -> list[WindowInfo]:
    """Visible, uncloaked top-level windows in z order (front first)."""
    if not _windows():
        return []
    from ctypes import wintypes

    user32 = _user32()
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _data):
        if user32.IsWindowVisible(hwnd) and not _cloaked(hwnd):
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    found = []
    for hwnd in handles:
        info = window_info(hwnd)
        left, top, right, bottom = info.rect
        if right - left > 1 and bottom - top > 1:
            found.append(info)
    return found


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(_user32().IsWindow(hwnd)) and bool(_user32().IsWindowVisible(hwnd))


def activate(hwnd: int) -> bool:
    """Bring a window to the front (restoring it when minimized)."""
    user32 = _user32()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if user32.GetForegroundWindow() == hwnd:
        return True
    # Only the foreground process may change focus; a synthetic Alt makes us eligible.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 0x2, 0)
    return user32.GetForegroundWindow() == hwnd


def title_score(wanted: str | None, actual: str) -> int:
    """3 exact, 2 contains either way, 0 otherwise (case-insensitive)."""
    if wanted is None:
        return 2
    a, b = wanted.strip().casefold(), actual.strip().casefold()
    if a == b:
        return 3
    if a and b and (a in b or b in a):
        return 2
    return 0


def match_window(
    windows: list[WindowInfo], title: str | None, process: str | None, class_name: str | None, exact: bool = False
) -> WindowInfo | None:
    """Best window for a recorded identity; tolerant to title changes of the same app.

    Process and class must match when given. Title: exact > contains > (same
    process and class, only when no window matches the title at all).
    """
    candidates = [
        w for w in windows
        if (not process or w.process.casefold() == process.casefold())
        and (not class_name or w.class_name == class_name)
    ]
    if not candidates:
        return None
    if title is None or title == "":
        return candidates[0] if (process or class_name) else None
    best, best_score = None, 0
    for window in candidates:
        score = title_score(title, window.title)
        if exact and score < 3:
            continue
        if score > best_score:
            best, best_score = window, score
    if best is not None:
        return best
    if not exact and process and class_name:
        return candidates[0]
    return None


# ---------------------------------------------------------------- elements


def element_from_handle(hwnd: int):
    _iuia, info_type = load()
    return info_type(hwnd)


def element_at(x: int, y: int):
    _iuia, info_type = load()
    return info_type.from_point(x, y)


def focused_element():
    iuia, info_type = load()
    try:
        return info_type(iuia().iuia.GetFocusedElement())
    except Exception:
        return None


def is_password(element) -> bool:
    try:
        return bool(element.element.CurrentIsPassword)
    except Exception:
        return False


def runtime_id(element) -> str:
    try:
        return ".".join(str(part) for part in element.runtime_id)
    except Exception:
        return ""


def rect_of(element) -> tuple[int, int, int, int]:
    r = element.rectangle
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def same_element(a, b) -> bool:
    if a is None or b is None:
        return False
    try:
        iuia, _info = load()
        return bool(iuia().iuia.CompareElements(a.element, b.element))
    except Exception:
        return False


def find_all(root, *, name: str | None = None, role: str | None = None,
             auto_id: str | None = None, class_name: str | None = None) -> list:
    """Native FindAll over `root` descendants with every given property condition."""
    iuia, _info = load()
    from pywinauto.uia_element_info import elements_from_uia_array

    automation = iuia()
    dll = automation.UIA_dll
    conditions = []
    if role:
        control_type = automation.known_control_types.get(role)
        if control_type is None:
            return []
        conditions.append(automation.iuia.CreatePropertyCondition(dll.UIA_ControlTypePropertyId, control_type))
    if name:
        conditions.append(automation.iuia.CreatePropertyCondition(dll.UIA_NamePropertyId, name))
    if auto_id:
        conditions.append(automation.iuia.CreatePropertyCondition(dll.UIA_AutomationIdPropertyId, auto_id))
    if class_name:
        conditions.append(automation.iuia.CreatePropertyCondition(dll.UIA_ClassNamePropertyId, class_name))
    if not conditions:
        return []
    condition = conditions[0] if len(conditions) == 1 else automation.iuia.CreateAndConditionFromArray(conditions)
    try:
        found = root.element.FindAll(automation.tree_scope["descendants"], condition)
        return list(elements_from_uia_array(found))
    except Exception:
        return []


def top_level_of(element):
    """Walk up to the child of the desktop root (the element's top-level window)."""
    _iuia, info_type = load()
    root = info_type()
    current, parent = element, element.parent
    hops = 0
    while parent is not None and parent != root and hops < 60:
        current, parent, hops = parent, parent.parent, hops + 1
    return current


def describe(element) -> dict[str, Any]:
    """Plain identity of an element as stored in recordings."""
    def safe(getter, default=""):
        try:
            value = getter()
            return default if value is None else value
        except Exception:
            return default

    return {
        "name": str(safe(lambda: element.name))[:300],
        "role": str(safe(lambda: element.control_type)),
        "auto_id": str(safe(lambda: element.automation_id))[:200],
        "class": str(safe(lambda: element.class_name))[:200],
        "rect": list(safe(lambda: rect_of(element), (0, 0, 0, 0))),
        "password": is_password(element),
        "runtime_id": runtime_id(element),
        "handle": int(safe(lambda: element.handle, 0) or 0),
        "enabled": bool(safe(lambda: element.enabled, True)),
    }


def stable_auto_id(info: dict[str, Any]) -> str:
    """AutomationId worth recording: Win32 controls without a name expose their HWND
    (WinForms) or a large numeric id that changes on every run; dialog ids are 16-bit."""
    auto_id = str(info.get("auto_id") or "")
    if auto_id.isdigit() and (int(auto_id) > 0xFFFF or int(auto_id) == int(info.get("handle") or -1)):
        return ""
    return auto_id


def usable_selector(info: dict[str, Any], window_rect_: tuple[int, int, int, int] | list[int] | None = None) -> bool:
    """Whether Name/AutomationId/role identify the element well enough to search by UIA."""
    name, auto_id, role = info.get("name") or "", stable_auto_id(info), info.get("role") or ""
    if not role or role == "Unknown":
        return False
    rect = info.get("rect") or [0, 0, 0, 0]
    if window_rect_ and list(rect) == list(window_rect_):
        return False  # the element IS the window: UIA sees no inner control
    if role in WEAK_ROLES and not auto_id and not name:
        return False
    return bool(name or auto_id)


def element_value(element) -> str:
    """Visible text: ValuePattern value, else the element name."""
    try:
        pattern = element.element.GetCurrentPattern(10002)  # UIA_ValuePatternId
        if pattern:
            from comtypes.gen import UIAutomationClient

            value = pattern.QueryInterface(UIAutomationClient.IUIAutomationValuePattern).CurrentValue
            if value is not None:
                return str(value)
    except Exception:
        pass
    try:
        return str(element.name or "")
    except Exception:
        return ""
