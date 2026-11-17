import ctypes
import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .icons import app_icon
from .main_window import MainWindow
from .settings import APP_NAME, Settings
from .theme import apply_theme

INSTANCE_KEY = "spona-adb-tool-instance"


def _bring_to_front(server: QLocalServer, window: MainWindow) -> None:
    connection = server.nextPendingConnection()
    if connection is not None:
        connection.disconnectFromServer()
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()


def main() -> int:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Spona.AdbTool")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_KEY)
    if socket.waitForConnected(250):
        socket.disconnectFromServer()
        return 0
    apply_theme(app)
    app.setWindowIcon(app_icon())
    window = MainWindow(Settings())
    server = QLocalServer(window)
    QLocalServer.removeServer(INSTANCE_KEY)
    server.listen(INSTANCE_KEY)
    server.newConnection.connect(lambda: _bring_to_front(server, window))
    window.show()
    return app.exec()
