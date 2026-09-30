"""Native desktop administration shell and application entry point."""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QMessageBox, QSplitter, QStackedWidget,
    QStyle, QSystemTrayIcon, QToolBar, QTreeWidget, QTreeWidgetItem,
)

from .bridge import ServerBridge
from .chat_page import ChatPage
from .dialogs import BroadcastDialog
from .management_pages import (
    AccountsPage, BansPage, ChannelsPage, ConnectionsPage, LogsPage,
    OperatorsPage, OverviewPage, StatisticsPage,
)
from .settings_page import SettingsPage, SetupWizard
from .widgets import brand_icon, confirm, duration, export_text, value


def plain(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {key: plain(item) for key, item in obj.items()}
    if isinstance(obj, (tuple, list, set, frozenset)):
        return [plain(item) for item in obj]
    return obj


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, *, bridge: Any = None, start_bridge: bool = True, debug: bool = False):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.setWindowTitle("OpenIRC — Server Administration")
        self.setWindowIcon(brand_icon("stopped"))
        self.resize(1240, 820)
        self.setMinimumSize(900, 600)
        self.snapshot: Any = None
        self._callbacks: dict[int, tuple[Callable | None, Callable | None, bool, str]] = {}
        self._snapshot_pending = False
        self._initialized = False
        self._setup_shown = False
        self._closing = False
        self._force_exit = False
        self._shutdown_complete = False
        self.gui_settings = QSettings(str(self.data_dir / "gui.ini"), QSettings.Format.IniFormat)
        self.bridge = bridge or ServerBridge(self.data_dir, debug=debug)
        self.bridge.ready.connect(self.service_ready)
        self.bridge.completed.connect(self.completed)
        self.bridge.failed.connect(self.failed)
        self.bridge.closed.connect(self.service_closed)
        self.splitter = QSplitter()
        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setMinimumWidth(180)
        self.nav.setMaximumWidth(350)
        self.nav.setIndentation(16)
        self.stack = QStackedWidget()
        self.splitter.addWidget(self.nav)
        self.splitter.addWidget(self.stack)
        self.splitter.setSizes([210, 1030])
        self.splitter.setStretchFactor(1, 1)
        self.setCentralWidget(self.splitter)
        self.pages = {
            "Overview": OverviewPage(self), "Connections": ConnectionsPage(self),
            "Statistics": StatisticsPage(self), "Channels": ChannelsPage(self),
            "Chat": ChatPage(self), "Accounts": AccountsPage(self),
            "Operators": OperatorsPage(self), "Bans / Access": BansPage(self),
            "Logs / Audit": LogsPage(self), "Settings": SettingsPage(self),
        }
        self.nav_items = {}
        for group, names in [("Server", ["Overview", "Connections", "Statistics"]), ("Community", ["Channels", "Chat", "Accounts"]), ("Security", ["Operators", "Bans / Access"]), ("System", ["Logs / Audit", "Settings"])]:
            parent = QTreeWidgetItem(self.nav, [group])
            parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            for name in names:
                item = QTreeWidgetItem(parent, [name])
                self.nav_items[name] = item
                item.setData(0, Qt.ItemDataRole.UserRole, name)
                self.stack.addWidget(self.pages[name])
            parent.setExpanded(True)
        self.nav.currentItemChanged.connect(self.navigation_changed)
        self.build_actions()
        self.build_menus()
        self.build_toolbar()
        self.build_tray()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.statusBar().showMessage("Opening server configuration…")
        geometry = self.gui_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter = self.gui_settings.value("splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        self.show_page("Overview")
        self.update_actions()
        if start_bridge:
            QTimer.singleShot(0, self.bridge.start)

    def build_actions(self) -> None:
        style = self.style()
        definitions = {
            "start": ("Start Server", lambda: self.command("start"), QStyle.StandardPixmap.SP_MediaPlay),
            "stop": ("Stop Server", self.stop_server, QStyle.StandardPixmap.SP_MediaStop),
            "restart": ("Restart Server", self.restart_server, QStyle.StandardPixmap.SP_BrowserReload),
            "refresh": ("Refresh", self.refresh, QStyle.StandardPixmap.SP_BrowserReload),
            "broadcast": ("Broadcast…", self.broadcast, QStyle.StandardPixmap.SP_MessageBoxInformation),
            "settings": ("Settings", lambda: self.show_page("Settings"), QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "export": ("Export Configuration…", self.export_configuration, QStyle.StandardPixmap.SP_DialogSaveButton),
            "exit": ("Exit", self.exit_application, QStyle.StandardPixmap.SP_DialogCloseButton),
        }
        self.actions = {}
        for key, (label, callback, icon) in definitions.items():
            action = QAction(style.standardIcon(icon), label, self)
            action.triggered.connect(lambda checked=False, cb=callback: cb())
            self.actions[key] = action
        self.actions["refresh"].setShortcut(QKeySequence.StandardKey.Refresh)
        self.actions["exit"].setShortcut(QKeySequence.StandardKey.Quit)
        self.actions["start"].setShortcut("Ctrl+R")

    def build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.actions["export"])
        file_menu.addSeparator()
        file_menu.addAction(self.actions["exit"])
        server = self.menuBar().addMenu("&Server")
        for name in ["start", "stop", "restart", "broadcast", "settings"]:
            server.addAction(self.actions[name])
        users = self.menuBar().addMenu("&Users")
        for title, page in [("Accounts", "Accounts"), ("Connections", "Connections"), ("Operators", "Operators"), ("Bans", "Bans / Access")]:
            users.addAction(title, lambda checked=False, selected=page: self.show_page(selected))
        channels = self.menuBar().addMenu("&Channels")
        channels.addAction("Create channel…", self.pages["Channels"].create)
        channels.addAction("Registered channels", lambda: self.channel_filter(2))
        channels.addAction("Active channels", lambda: self.channel_filter(1))
        channels.addAction("Open chat", lambda: self.show_page("Chat"))
        tools = self.menuBar().addMenu("&Tools")
        tools.addAction("View logs", lambda: self.show_page("Logs / Audit"))
        tools.addAction("Statistics", lambda: self.show_page("Statistics"))
        tools.addAction("TLS certificate instructions", self.tls_instructions)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("OpenIRC documentation", lambda: QDesktopServices.openUrl(QUrl("https://github.com/1ntrusi0n/OpenIRC#readme")))
        help_menu.addAction("Protocol information", lambda: QDesktopServices.openUrl(QUrl("https://github.com/1ntrusi0n/OpenIRC/blob/main/docs/ircx-support.md")))
        help_menu.addSeparator()
        help_menu.addAction("About OpenIRC", self.about)

    def build_toolbar(self) -> None:
        toolbar = QToolBar("Server", self)
        toolbar.setObjectName("serverToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        for name in ["start", "stop", "refresh", "broadcast", "settings"]:
            toolbar.addAction(self.actions[name])

    def build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(brand_icon("stopped"), self)
        self.tray.setToolTip("OpenIRC · Stopped")
        menu = QMenu(self)
        menu.addAction("Open OpenIRC", self.restore_window)
        menu.addSeparator()
        menu.addAction(self.actions["start"])
        menu.addAction(self.actions["stop"])
        self.tray_status = menu.addAction("Status: Stopped")
        self.tray_status.setEnabled(False)
        menu.addSeparator()
        menu.addAction(self.actions["exit"])
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)

    def restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def navigation_changed(self, item: QTreeWidgetItem | None, previous: Any) -> None:
        if item:
            name = item.data(0, Qt.ItemDataRole.UserRole)
            if name in self.pages:
                self.stack.setCurrentWidget(self.pages[name])

    def show_page(self, name: str) -> None:
        self.nav.setCurrentItem(self.nav_items[name])

    def channel_filter(self, index: int) -> None:
        self.show_page("Channels")
        self.pages["Channels"].kind.setCurrentIndex(index)

    def service_ready(self) -> None:
        self._initialized = True
        self.timer.start()
        self.refresh()

    def command(self, action: str, payload: dict | None = None, callback: Callable | None = None, failure: Callable | None = None, refresh: bool = True) -> None:
        if self._closing:
            return
        if not self._initialized:
            self.statusBar().showMessage("The server service is still opening.")
            return
        if len(self._callbacks) >= 128:
            message = "The server is still processing previous requests. Try again shortly."
            if failure:
                failure(message)
            else:
                self.statusBar().showMessage(message)
            return
        token = self.bridge.request(action, payload)
        self._callbacks[token] = (callback, failure, refresh, action)

    def refresh(self) -> None:
        if self._initialized and not self._snapshot_pending and not self._closing:
            self._snapshot_pending = True
            self.command("snapshot", callback=self.receive_snapshot, failure=self.snapshot_failed, refresh=False)

    def snapshot_failed(self, message: str) -> None:
        self._snapshot_pending = False
        self.statusBar().showMessage(f"Refresh failed: {message}")

    def completed(self, token: int, result: Any) -> None:
        callback, _, refresh, action = self._callbacks.pop(token, (None, None, False, ""))
        if callback:
            callback(result)
        elif action and not action.startswith("chat."):
            self.statusBar().showMessage("Operation completed.", 4000)
        if refresh:
            self.refresh()

    def failed(self, token: int, message: str) -> None:
        _, failure, _, action = self._callbacks.pop(token, (None, None, False, ""))
        if failure:
            failure(message)
            return
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "OpenIRC could not complete the action", message)
        if token == 0:
            self.timer.stop()

    def receive_snapshot(self, snapshot: Any) -> None:
        self._snapshot_pending = False
        self.snapshot = snapshot
        for page in self.pages.values():
            page.refresh(snapshot)
        self.update_actions()
        status = str(value(snapshot, "status", "stopped"))
        statistics = value(snapshot, "statistics", {})
        self.statusBar().showMessage(f"{status.capitalize()}  |  Users: {len(value(snapshot, 'connections', ()))}  |  Channels: {len(value(snapshot, 'channels', ()))}  |  Uptime: {duration(value(statistics, 'uptime', 0))}")
        if self.tray:
            self.tray.setIcon(brand_icon(status.lower()))
            self.tray.setToolTip(f"OpenIRC · {status.capitalize()}")
            self.tray_status.setText(f"Status: {status.capitalize()}")
            self.tray.setVisible(value(value(snapshot, "settings", {}), "minimize_to_tray", False))
        if not self._setup_shown:
            self._setup_shown = True
            if not value(value(snapshot, "settings", {}), "setup_complete", False):
                QTimer.singleShot(0, self.first_run)
            elif value(value(snapshot, "settings", {}), "auto_start", False):
                self.command("start")

    def update_actions(self) -> None:
        running = str(value(self.snapshot, "status", "stopped")).lower() == "running"
        self.actions["start"].setEnabled(self._initialized and not running and not self._closing)
        self.actions["stop"].setEnabled(running and not self._closing)
        self.actions["restart"].setEnabled(running and not self._closing)
        self.actions["broadcast"].setEnabled(running and not self._closing)
        self.actions["refresh"].setEnabled(self._initialized and not self._closing)
        self.actions["export"].setEnabled(self._initialized and not self._closing)

    def first_run(self) -> None:
        wizard = SetupWizard(self)
        if wizard.exec():
            self.command("setup", wizard.payload(), failure=self.setup_failed)
        else:
            self.statusBar().showMessage("Setup cancelled. Create accounts and configure the server before starting.")

    def setup_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Setup needs attention", message)
        QTimer.singleShot(0, self.first_run)

    def stop_server(self) -> None:
        settings = value(self.snapshot, "settings", {})
        if value(settings, "confirm_shutdown", True) and not confirm(self, "Stop server", "Disconnect all clients and stop listening? The console will remain open."):
            return
        self.command("stop")

    def restart_server(self) -> None:
        if confirm(self, "Restart server", "Disconnect all clients and restart listeners with the saved settings?"):
            self.command("restart")

    def broadcast(self) -> None:
        dialog = BroadcastDialog(self, value(self.snapshot, "channels", ()))
        if dialog.exec():
            self.command("broadcast", dialog.payload())

    def export_configuration(self) -> None:
        self.command("configuration.export", callback=lambda result: export_text(self, json.dumps(plain(result), ensure_ascii=False, indent=2), "openirc-configuration.json"), refresh=False)

    def tls_instructions(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("TLS certificate instructions")
        dialog.setText("For public use, obtain a PEM certificate and private key for your server hostname from a certificate authority. Select both files under Settings → Listening, enable TLS, and restart the server.")
        dialog.setInformativeText("For local testing, create a self-signed certificate with OpenSSL. Clients must explicitly trust that test certificate.")
        dialog.setDetailedText("openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes -keyout server.key -out server.crt -subj /CN=localhost\n\nKeep the private key in your data directory with access limited to the server account. Never commit it to the repository.")
        dialog.exec()

    def about(self) -> None:
        from OpenIRC import __version__
        QMessageBox.about(self, "About OpenIRC", f"OpenIRC\nOpen-source IRC/IRCX Server\n\nVersion {__version__}\n\nIRC is an Internet protocol. IRCX interoperability is based on publicly documented protocol specifications.\n\nOriginal OpenIRC source is licensed under MIT. PyQt6 is distributed under its own GPL/commercial license; see dependency notices.")

    def exit_application(self) -> None:
        self._force_exit = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutdown_complete:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        settings = value(self.snapshot, "settings", {})
        if self.tray and value(settings, "minimize_to_tray", False) and not self._force_exit:
            self.hide()
            event.ignore()
            return
        running = str(value(self.snapshot, "status", "stopped")).lower() == "running"
        if running and value(settings, "confirm_shutdown", True) and not confirm(self, "Exit OpenIRC", "Stop the server, disconnect clients, and exit OpenIRC?"):
            self._force_exit = False
            event.ignore()
            return
        self.gui_settings.setValue("geometry", self.saveGeometry())
        self.gui_settings.setValue("splitter", self.splitter.saveState())
        self.gui_settings.sync()
        self._closing = True
        self.timer.stop()
        self.pages["Chat"].timer.stop()
        self.setEnabled(False)
        self.statusBar().showMessage("Closing listeners and saving server state…")
        self.bridge.close()
        event.ignore()

    def service_closed(self) -> None:
        self._shutdown_complete = True
        if self.tray:
            self.tray.hide()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()


def run_gui(data_dir: Path, debug: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("OpenIRC")
    app.setOrganizationName("OpenIRC")
    app.setWindowIcon(brand_icon("stopped"))
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(Path(data_dir), debug=debug)
    window.show()
    return app.exec()


launch = run_gui
