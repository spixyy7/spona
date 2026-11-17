import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

BACKGROUND = "#131417"
SURFACE = "#1a1b1f"
SURFACE_HIGH = "#23252a"
BORDER = "#2a2c32"
TEXT = "#e3e5e8"
MUTED = "#8a8f98"
ACCENT = "#3ddc84"
ACCENT_DARK = "#0c1f14"
ACCENT_TINT = "#1b2a21"
DANGER = "#ef5b5b"
WARNING = "#f2b84b"
IDLE = "#5c5f66"

STYLE = f"""
QWidget#root {{ background: {BACKGROUND}; }}
QFrame#topbar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame#bottombar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QFrame#separator {{ background: {BORDER}; }}
QLabel#muted {{ color: {MUTED}; }}
QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px; color: {TEXT}; }}
QToolButton:hover {{ background: {SURFACE_HIGH}; }}
QToolButton:pressed {{ background: #2c2f35; }}
QToolButton:disabled {{ color: #55585f; }}
QToolButton:checked {{ background: {ACCENT_TINT}; color: {ACCENT}; }}
QToolButton:checked:hover {{ background: #20332a; }}
QToolButton#path {{ color: {MUTED}; padding: 5px 8px; }}
QToolButton#path:hover {{ color: {TEXT}; }}
QToolButton#chip {{ background: {BACKGROUND}; border: 1px solid {BORDER}; border-radius: 14px; padding: 4px 12px 4px 8px; color: {MUTED}; }}
QToolButton#chip:hover {{ border-color: #3a3d44; color: {TEXT}; }}
QToolButton#chip:checked {{ background: {ACCENT_TINT}; border-color: {ACCENT}; color: {TEXT}; }}
QToolButton#chip:checked:hover {{ background: #20332a; }}
QPushButton#capture {{ background: {ACCENT}; color: {ACCENT_DARK}; border: none; border-radius: 10px; padding: 8px 18px 8px 14px; font-weight: 600; }}
QPushButton#capture:hover {{ background: #5be69c; }}
QPushButton#capture:pressed {{ background: #2fc472; }}
QPushButton#capture:disabled {{ background: #22332a; color: #5d7a69; }}
QListWidget#gallery {{ background: {BACKGROUND}; border: none; border-top: 1px solid {BORDER}; padding: 6px 8px 2px 8px; outline: none; }}
QScrollBar:horizontal {{ height: 6px; background: transparent; margin: 0 8px; }}
QScrollBar::handle:horizontal {{ background: #33363c; border-radius: 3px; min-width: 40px; }}
QScrollBar::handle:horizontal:hover {{ background: #464a52; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
QToolTip {{ background: {SURFACE_HIGH}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 8px; border-radius: 6px; }}
QMenu {{ background: {SURFACE_HIGH}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2c3a32; }}
QLabel#toast {{ background: rgba(28, 30, 34, 242); color: {TEXT}; border: 1px solid #353840; border-radius: 10px; padding: 8px 14px; }}
QLabel#toast[kind="ok"] {{ border-color: {ACCENT}; }}
QLabel#toast[kind="error"] {{ border-color: {DANGER}; }}
QFrame#overlay {{ background: rgba(20, 21, 24, 225); border: 1px solid #2f3238; border-radius: 12px; }}
QDialog#dialog {{ background: {SURFACE}; }}
QLabel#dialogTitle {{ font-size: 15px; font-weight: 600; }}
QLineEdit {{ background: {BACKGROUND}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 10px; selection-background-color: #2c3a32; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QCheckBox {{ color: {MUTED}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a3d44; background: {BACKGROUND}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QDialog QPushButton {{ background: {SURFACE_HIGH}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 16px; }}
QDialog QPushButton:hover {{ border-color: #3a3d44; }}
QDialog QPushButton#primary {{ background: {ACCENT}; border: none; color: {ACCENT_DARK}; font-weight: 600; }}
QDialog QPushButton#primary:hover {{ background: #5be69c; }}
QDialog QPushButton#danger {{ background: transparent; border: 1px solid {DANGER}; color: {DANGER}; font-weight: 600; }}
QDialog QPushButton#danger:hover {{ background: rgba(239, 91, 91, 30); }}
QDialog QPushButton#danger:disabled {{ border-color: #3a3d44; color: #55585f; }}
QDialog QPushButton:focus {{ outline: none; }}
QLabel#section {{ color: {TEXT}; font-weight: 600; }}
QRadioButton {{ spacing: 10px; padding: 4px 0; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid #3a3d44; background: {BACKGROUND}; }}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ border: 1px solid {ACCENT}; background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5, stop: 0 {ACCENT}, stop: 0.48 {ACCENT}, stop: 0.56 {BACKGROUND}, stop: 1 {BACKGROUND}); }}
QLabel#counter {{ color: {MUTED}; padding: 0 6px; }}
QSlider {{ background: transparent; min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; background: #34373d; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: #e3e5e8; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
"""


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: BACKGROUND,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_HIGH,
        QPalette.ColorRole.ToolTipBase: SURFACE_HIGH,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE_HIGH,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ACCENT_DARK,
        QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5a5e66"))
    app.setPalette(palette)
    families = set(QFontDatabase.families())
    family = next(
        (name for name in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI") if name in families),
        app.font().family(),
    )
    font = QFont(family)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(STYLE)


def _colorref(color: str) -> int:
    value = QColor(color)
    return value.red() | (value.green() << 8) | (value.blue() << 16)


def style_title_bar(widget, caption: str = SURFACE, text: str = TEXT) -> None:
    if sys.platform != "win32":
        return
    try:
        dwm = ctypes.windll.dwmapi
        handle = ctypes.c_void_p(int(widget.winId()))
        for attribute, value in ((20, 1), (35, _colorref(caption)), (36, _colorref(text))):
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(handle, ctypes.c_uint(attribute), ctypes.byref(data), ctypes.sizeof(data))
    except (AttributeError, OSError):
        pass
