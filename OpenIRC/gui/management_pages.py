"""Live management pages, backed exclusively by the administration service."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from .dialogs import AccessEditor, AccountEditor, BanEditor, ChannelEditor, OperatorEditor, UserDetails
from .widgets import DataTable, FormDialog, Page, confirm, display, duration, export_text, timestamp, value


def menu_for(table: DataTable, point: Any, actions: list[tuple[str, Any]]) -> None:
    if not table.selected():
        return
    menu = QMenu(table)
    for title, callback in actions:
        menu.addAction(title, callback)
    menu.exec(table.viewport().mapToGlobal(point))


class OverviewPage(Page):
    def __init__(self, controller: Any):
        super().__init__("OpenIRC Server", "Manage your community, monitor activity, and configure access from this console.")
        self.controller = controller
        self.endpoint = QLabel("Loading server configuration…")
        self.endpoint.setWordWrap(True)
        self.layout.addWidget(self.endpoint)
        self.cards = {}
        grid = QGridLayout()
        for index, title in enumerate(["Server status", "Users", "Channels", "Connections", "Uptime", "Inbound traffic", "Outbound traffic", "Accounts"]):
            group = QGroupBox(title)
            inner = QVBoxLayout(group)
            label = QLabel("—")
            font = label.font()
            font.setPointSize(font.pointSize() + 8)
            label.setFont(font)
            inner.addWidget(label)
            grid.addWidget(group, index // 4, index % 4)
            self.cards[title] = label
        self.layout.addLayout(grid)
        controls = self.buttons([("Start Server", lambda: controller.command("start")), ("Stop Server", controller.stop_server)])
        self.start_button = controls.itemAt(0).widget()
        self.stop_button = controls.itemAt(1).widget()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.layout.addWidget(QLabel("Recent activity"))
        self.activity = DataTable([("Time", lambda r: timestamp(value(r, "time"))), ("Level", "level"), ("Category", "category"), ("Message", "message")], key="time")
        self.layout.addWidget(self.activity)

    def refresh(self, snapshot: Any) -> None:
        statistics = value(snapshot, "statistics", {})
        settings = value(statistics, "runtime_settings", value(snapshot, "settings", {}))
        connections = value(snapshot, "connections", ())
        cards = {"Server status": str(value(snapshot, "status", "stopped")).capitalize(), "Users": len(connections), "Channels": len(value(snapshot, "channels", ())), "Connections": value(statistics, "current_connections", len(connections)), "Uptime": duration(value(statistics, "uptime", 0)), "Inbound traffic": f"{int(value(statistics, 'bytes_received', 0)):,} bytes", "Outbound traffic": f"{int(value(statistics, 'bytes_sent', 0)):,} bytes", "Accounts": len(value(snapshot, "accounts", ()))}
        for title, text in cards.items():
            self.cards[title].setText(str(text))
        running = str(value(snapshot, "status")).lower() == "running"
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        tls = f"TLS {value(settings, 'tls_port')}" if value(settings, "tls_enabled", False) else "TLS disabled"
        bound = value(statistics, "bound_addresses", ())
        endpoints = ", ".join(f"[{address[0]}]:{address[1]}" if ":" in address[0] else f"{address[0]}:{address[1]}" for address in bound)
        if not endpoints:
            endpoints = f"{value(settings, 'bind_address')}:{value(settings, 'irc_port')}"
        self.endpoint.setText(f"{value(settings, 'server_name')} · {value(settings, 'network_name')}\n{endpoints} · {tls}")
        self.activity.set_records(value(snapshot, "logs", ())[-100:])


class ConnectionsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Connections", "Live network and local chat sessions. Select a user to inspect or moderate.")
        self.controller = controller
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter connections…")
        self.layout.addWidget(self.search)
        self.table = DataTable([("Nickname", "nick"), ("Account", "account"), ("Username", "username"), ("Host / IP", "ip"), ("Connected", lambda r: timestamp(value(r, "connected_at"))), ("Idle", lambda r: duration(value(r, "idle"))), ("Channels", "channels"), ("IRCX", "ircx"), ("Transport", "transport"), ("Operator", "operator"), ("Bytes in", "bytes_in"), ("Bytes out", "bytes_out")])
        self.layout.addWidget(self.table)
        self.search.textChanged.connect(self.table.filter_rows)
        self.actions = [("View details", self.details), ("Send notice", self.notice), ("Kill connection", self.kill), ("Force join channel", self.join), ("Force part channel", self.part), ("Change nickname", self.nickname), ("Ban", self.ban), ("Copy host / IP", self.copy_host)]
        self.buttons(self.actions[:3], self.table)
        self.table.customContextMenuRequested.connect(lambda point: menu_for(self.table, point, self.actions))
        self.table.cellDoubleClicked.connect(lambda *_: self.details())

    def refresh(self, snapshot: Any) -> None:
        self.table.set_records(value(snapshot, "connections", ()))

    def details(self) -> None:
        record = self.table.selected()
        if record:
            UserDetails(self, record).exec()

    def prompt(self, action: str, title: str, label: str, key: str, initial: str = "") -> None:
        record = self.table.selected()
        if record:
            text, ok = QInputDialog.getText(self, title, label, text=initial)
            if ok and text.strip():
                self.controller.command(action, {"id": value(record, "id"), key: text.strip()})

    def notice(self) -> None:
        self.prompt("connection.notice", "Send notice", "Message", "message")

    def join(self) -> None:
        self.prompt("connection.join", "Force join", "Channel", "channel", "#")

    def part(self) -> None:
        self.prompt("connection.part", "Force part", "Channel", "channel", "#")

    def nickname(self) -> None:
        self.prompt("connection.nick", "Change nickname", "New nickname", "nickname")

    def kill(self) -> None:
        record = self.table.selected()
        if record and confirm(self, "Kill connection", f"Disconnect {value(record, 'nick')} now?"):
            self.controller.command("connection.kill", {"id": value(record, "id"), "reason": "Disconnected by administrator"})

    def ban(self) -> None:
        record = self.table.selected()
        if record:
            editor = BanEditor(self, mask=value(record, "ip"))
            editor.kind.setCurrentText("ip")
            if editor.exec():
                self.controller.command("ban.save", editor.payload())

    def copy_host(self) -> None:
        record = self.table.selected()
        if record:
            QApplication.clipboard().setText(value(record, "ip"))


class ChannelsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Channels", "Registered channels retain their properties and access rules after restart.")
        self.controller = controller
        self.editor: ChannelEditor | None = None
        filter_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter channels…")
        self.kind = QComboBox()
        self.kind.addItems(["All channels", "Active channels", "Registered channels", "Temporary channels"])
        filter_bar.addWidget(self.search)
        filter_bar.addWidget(self.kind)
        self.layout.addLayout(filter_bar)
        self.table = DataTable([("Channel", "name"), ("Users", "users"), ("Owners", "owners"), ("Hosts", "hosts"), ("Voiced", "voiced"), ("Modes", "modes"), ("Topic", "topic"), ("Registered", "registered"), ("Created", lambda r: timestamp(value(r, "created_at")))])
        self.layout.addWidget(self.table)
        self.search.textChanged.connect(self.table.filter_rows)
        self.kind.currentIndexChanged.connect(lambda: self.refresh(controller.snapshot))
        self.buttons([("Create channel", self.create), ("Refresh", controller.refresh)])
        self.buttons([("Edit", self.edit), ("Register", lambda: self.channel_action("register")), ("Unregister", lambda: self.channel_action("unregister")), ("Force close", lambda: self.channel_action("close")), ("Delete", lambda: self.channel_action("delete"))], self.table)
        self.actions = [("Channel properties", self.edit), ("View members", lambda: self.edit("Members")), ("Access list", lambda: self.edit("Access")), ("Set topic", self.topic), ("Set modes", lambda: self.edit("Modes")), ("Broadcast to channel", self.broadcast), ("Join in chat", self.join_chat), ("Force close", lambda: self.channel_action("close"))]
        self.table.customContextMenuRequested.connect(lambda point: menu_for(self.table, point, self.actions))
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())

    def refresh(self, snapshot: Any) -> None:
        records = value(snapshot, "channels", ())
        choice = self.kind.currentIndex()
        if choice == 1:
            records = [r for r in records if value(r, "users", 0)]
        elif choice == 2:
            records = [r for r in records if value(r, "registered", False)]
        elif choice == 3:
            records = [r for r in records if not value(r, "registered", False)]
        self.table.set_records(records)
        if self.editor:
            self.editor.refresh(snapshot)

    def create(self) -> None:
        self.open_editor(None)

    def edit(self, tab: str = "General") -> None:
        record = self.table.selected()
        if record:
            self.open_editor(record, tab)

    def open_editor(self, channel: Any, tab: str = "General") -> None:
        self.editor = ChannelEditor(self, channel, value(self.controller.snapshot, "accounts", ()), self.controller, tab)
        if self.editor.exec():
            self.controller.command("channel.save", self.editor.payload())
        self.editor = None

    def channel_action(self, action: str) -> None:
        record = self.table.selected()
        if not record:
            return
        messages = {"close": "Remove every member while retaining the channel registration?", "unregister": "Remove the registration? Occupied channels remain active.", "delete": "Delete this channel and its registration?"}
        if action in messages and not confirm(self, action.capitalize() + " channel", messages[action]):
            return
        self.controller.command(f"channel.{action}", {"id": value(record, "id")})

    def topic(self) -> None:
        record = self.table.selected()
        if record:
            topic, ok = QInputDialog.getText(self, "Set channel topic", value(record, "name"), text=value(record, "topic"))
            if ok:
                self.controller.command("channel.topic", {"id": value(record, "id"), "topic": topic})

    def broadcast(self) -> None:
        record = self.table.selected()
        if record:
            message, ok = QInputDialog.getText(self, "Channel notice", "Message")
            if ok and message:
                self.controller.command("broadcast", {"target": value(record, "name"), "message": message})

    def join_chat(self) -> None:
        record = self.table.selected()
        if record:
            self.controller.show_page("Chat")
            self.controller.pages["Chat"].join_channel(value(record, "name"))


class AccountsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Accounts", "Accounts are independent of nicknames. Passwords can only be replaced.")
        self.controller = controller
        tabs = QTabWidget()
        self.layout.addWidget(tabs)
        account_page = QWidget()
        content = QVBoxLayout(account_page)
        self.table = DataTable([("Username", "username"), ("Display name", "display_name"), ("Role", "role"), ("Enabled", "enabled"), ("Locked", "locked"), ("Created", lambda r: timestamp(value(r, "created_at"))), ("Last login", lambda r: timestamp(value(r, "last_login")))])
        content.addWidget(self.table)
        tabs.addTab(account_page, "Accounts")
        self.actions = [("Edit account", self.edit), ("Reset password", self.password), ("Enable", lambda: self.toggle("enabled", True)), ("Disable", lambda: self.toggle("enabled", False)), ("Lock", lambda: self.toggle("locked", True)), ("Unlock", lambda: self.toggle("locked", False)), ("Delete account", self.delete)]
        self.table.customContextMenuRequested.connect(lambda point: menu_for(self.table, point, self.actions))
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        row = QHBoxLayout()
        for title, action in [("Create account", self.create)] + self.actions[:2]:
            button = QPushButton(title)
            button.clicked.connect(action)
            if title != "Create account":
                button.setEnabled(False)
                self.table.itemSelectionChanged.connect(lambda b=button: b.setEnabled(self.table.selected() is not None))
            row.addWidget(button)
        row.addStretch()
        content.addLayout(row)
        reservations = QWidget()
        layout = QVBoxLayout(reservations)
        self.reservations = DataTable([("Nickname", "nickname"), ("Account", "account"), ("Created", lambda r: timestamp(value(r, "created_at"))), ("Protected", "protected")], key="nickname")
        layout.addWidget(self.reservations)
        row = QHBoxLayout()
        for title, action in [("Reserve nickname", self.reserve), ("Remove reservation", self.unreserve)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            if title == "Remove reservation":
                button.setEnabled(False)
                self.reservations.itemSelectionChanged.connect(lambda b=button: b.setEnabled(self.reservations.selected() is not None))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        tabs.addTab(reservations, "Nickname reservations")

    def refresh(self, snapshot: Any) -> None:
        self.table.set_records(value(snapshot, "accounts", ()))
        self.reservations.set_records(value(snapshot, "reservations", ()))

    def create(self) -> None:
        editor = AccountEditor(self)
        if editor.exec():
            self.controller.command("account.save", editor.payload())

    def edit(self) -> None:
        record = self.table.selected()
        if record:
            editor = AccountEditor(self, record)
            if editor.exec():
                self.controller.command("account.save", editor.payload())

    def password(self) -> None:
        record = self.table.selected()
        if record:
            password, ok = QInputDialog.getText(self, "Reset password", "New password", QLineEdit.EchoMode.Password)
            if ok and password:
                self.controller.command("account.password", {"id": value(record, "id"), "password": password})

    def toggle(self, field: str, state: bool) -> None:
        record = self.table.selected()
        if record and confirm(self, "Update account", f"Set {field} to {display(state)} for {value(record, 'username')}?"):
            self.controller.command("account.save", {"id": value(record, "id"), field: state})

    def delete(self) -> None:
        record = self.table.selected()
        if record and confirm(self, "Delete account", f"Permanently delete {value(record, 'username')} and its operator grant?"):
            self.controller.command("account.delete", {"id": value(record, "id")})

    def reserve(self) -> None:
        editor = FormDialog("Reserve a nickname", self)
        nickname = editor.line("Nickname")
        account = QComboBox()
        for record in value(self.controller.snapshot, "accounts", ()):
            account.addItem(value(record, "username"), value(record, "id"))
        editor.form.addRow("Account", account)
        protected = QCheckBox("Protected")
        protected.setChecked(True)
        editor.form.addRow(protected)
        if editor.exec():
            self.controller.command("reservation.save", {"nickname": nickname.text().strip(), "account_id": account.currentData(), "protected": protected.isChecked()})

    def unreserve(self) -> None:
        record = self.reservations.selected()
        if record and confirm(self, "Remove reservation", f"Release {value(record, 'nickname')}?"):
            self.controller.command("reservation.delete", {"nickname": value(record, "nickname")})


class OperatorsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Operators", "Configure explicit server permissions separately from channel ownership and ordinary accounts.")
        self.controller = controller
        self.table = DataTable([("Account", "account"), ("Role", "role"), ("Permissions", "permissions"), ("Enabled", "enabled"), ("Last login", lambda r: timestamp(value(r, "last_login")))], key="account_id")
        self.layout.addWidget(self.table)
        self.buttons([("Add operator", self.add)])
        self.buttons([("Edit permissions", self.edit), ("Remove operator", self.delete)], self.table)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())

    def refresh(self, snapshot: Any) -> None:
        self.table.set_records(value(snapshot, "operators", ()))

    def add(self) -> None:
        self.open_editor(None)

    def edit(self) -> None:
        record = self.table.selected()
        if record:
            self.open_editor(record)

    def open_editor(self, record: Any) -> None:
        editor = OperatorEditor(self, value(self.controller.snapshot, "accounts", ()), record)
        if editor.exec():
            self.controller.command("operator.save", editor.payload())

    def delete(self) -> None:
        record = self.table.selected()
        if record and confirm(self, "Remove operator", f"Revoke operator access for {value(record, 'account')}?"):
            self.controller.command("operator.delete", {"account_id": value(record, "account_id")})


class BansPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Bans and access", "Manage connection bans and channel access. Expired entries stop applying automatically.")
        self.controller = controller
        tabs = QTabWidget()
        self.layout.addWidget(tabs)
        bans = QWidget()
        layout = QVBoxLayout(bans)
        self.table = DataTable([("Type", "type"), ("Mask", "mask"), ("Reason", "reason"), ("Enabled", "enabled"), ("Created by", "created_by"), ("Created", lambda r: timestamp(value(r, "created_at"))), ("Expires", lambda r: timestamp(value(r, "expires_at")))])
        layout.addWidget(self.table)
        bar = QHBoxLayout()
        for title, callback in [("Add ban", self.add), ("Edit", self.edit), ("Remove", self.delete), ("Disconnect matches", self.disconnect)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            if title != "Add ban":
                button.setEnabled(False)
                self.table.itemSelectionChanged.connect(lambda b=button: b.setEnabled(self.table.selected() is not None))
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        tabs.addTab(bans, "Server bans")
        access = QWidget()
        layout = QVBoxLayout(access)
        self.object = QComboBox()
        self.object.setEditable(True)
        self.object.setPlaceholderText("Select channel or enter an access object")
        self.object.currentTextChanged.connect(self.refresh_access)
        layout.addWidget(self.object)
        self.access = DataTable([("Level", "level"), ("Mask", "mask"), ("Reason", "reason"), ("Added by", "created_by"), ("Created", lambda r: timestamp(value(r, "created_at"))), ("Expires", lambda r: timestamp(value(r, "expires_at")))])
        layout.addWidget(self.access)
        bar = QHBoxLayout()
        for title, callback in [("Add entry", self.add_access), ("Remove entry", self.delete_access), ("Clear entries", self.clear_access)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)
        tabs.addTab(access, "Channel access")

    def refresh(self, snapshot: Any) -> None:
        self.table.set_records(value(snapshot, "bans", ()))
        names = [value(channel, "name") for channel in value(snapshot, "channels", ())]
        previous = self.object.currentText()
        self.object.blockSignals(True)
        self.object.clear()
        self.object.addItems(names)
        if previous:
            self.object.setCurrentText(previous)
        self.object.blockSignals(False)
        self.refresh_access()

    def refresh_access(self) -> None:
        for channel in value(self.controller.snapshot, "channels", ()):
            if value(channel, "name") == self.object.currentText():
                self.access.set_records(value(channel, "access", ()))
                return
        self.access.set_records(())

    def add(self) -> None:
        editor = BanEditor(self)
        if editor.exec():
            self.controller.command("ban.save", editor.payload())

    def edit(self) -> None:
        record = self.table.selected()
        if record:
            editor = BanEditor(self, record)
            if editor.exec():
                self.controller.command("ban.save", editor.payload())

    def delete(self) -> None:
        record = self.table.selected()
        if record and confirm(self, "Remove ban", f"Remove the ban for {value(record, 'mask')}?"):
            self.controller.command("ban.delete", {"id": value(record, "id")})

    def disconnect(self) -> None:
        record = self.table.selected()
        if record and confirm(self, "Disconnect matching users", "Disconnect all users matching this ban now?"):
            self.controller.command("ban.disconnect", {"id": value(record, "id")})

    def add_access(self) -> None:
        if not self.object.currentText():
            return
        editor = AccessEditor(self, self.object.currentText())
        if editor.exec():
            self.controller.command("access.change", editor.payload())

    def delete_access(self) -> None:
        entry = self.access.selected()
        if entry and confirm(self, "Remove access entry", f"Remove {value(entry, 'mask')}?"):
            self.controller.command("access.change", {"object": self.object.currentText(), "operation": "DELETE", "level": value(entry, "level"), "mask": value(entry, "mask")})

    def clear_access(self) -> None:
        if self.object.currentText() and confirm(self, "Clear access entries", "Remove every access entry for this object?"):
            self.controller.command("access.change", {"object": self.object.currentText(), "operation": "CLEAR"})


class LogsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Logs and audit", "The display is bounded. Clearing this view never deletes log files or audit records.")
        self.controller = controller
        self.cutoff = 0.0
        bar = QHBoxLayout()
        self.severity = QComboBox()
        self.severity.addItems(["All levels", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.category = QComboBox()
        self.category.addItem("All categories")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search visible logs…")
        self.pause = QCheckBox("Pause auto-scroll")
        for widget in [self.severity, self.category, self.search, self.pause]:
            bar.addWidget(widget)
        self.layout.addLayout(bar)
        self.tabs = QTabWidget()
        self.table = DataTable([("Time", lambda r: timestamp(value(r, "time"))), ("Level", "level"), ("Category", "category"), ("Message", "message")], key="time")
        self.audit = DataTable([("Time", lambda r: timestamp(value(r, "timestamp"))), ("Actor", "actor"), ("Action", "action"), ("Object type", "object_type"), ("Object", "object"), ("Summary", "summary")], key="id")
        self.tabs.addTab(self.table, "Live log")
        self.tabs.addTab(self.audit, "Administration audit")
        self.layout.addWidget(self.tabs)
        self.buttons([("Clear display", self.clear), ("Export visible", self.export), ("Open log folder", self.open_folder)])
        self.severity.currentTextChanged.connect(lambda: self.refresh(controller.snapshot))
        self.category.currentTextChanged.connect(lambda: self.refresh(controller.snapshot))
        self.search.textChanged.connect(self.table.filter_rows)
        self.search.textChanged.connect(self.audit.filter_rows)
        self.table.cellDoubleClicked.connect(lambda *_: self.details(self.table))
        self.audit.cellDoubleClicked.connect(lambda *_: self.details(self.audit))

    def refresh(self, snapshot: Any) -> None:
        logs = value(snapshot, "logs", ())
        categories = sorted({value(row, "category") for row in logs})
        previous = self.category.currentText()
        if [self.category.itemText(i) for i in range(1, self.category.count())] != categories:
            self.category.blockSignals(True)
            self.category.clear()
            self.category.addItems(["All categories"] + categories)
            self.category.setCurrentText(previous)
            self.category.blockSignals(False)
        logs = [row for row in logs if value(row, "time", 0) > self.cutoff and (self.severity.currentIndex() == 0 or value(row, "level") == self.severity.currentText()) and (self.category.currentIndex() == 0 or value(row, "category") == self.category.currentText())]
        self.table.set_records(logs[-2000:])
        self.audit.set_records(value(snapshot, "audit", ())[-500:])
        if not self.pause.isChecked():
            self.table.scrollToBottom()

    def clear(self) -> None:
        import time
        self.cutoff = time.time()
        self.table.set_records(())

    def export(self) -> None:
        table = self.table if self.tabs.currentIndex() == 0 else self.audit
        export_text(self, table.visible_text(), "openirc-log.tsv")

    def open_folder(self) -> None:
        directory = value(value(self.controller.snapshot, "settings", {}), "log_directory") or str(self.controller.data_dir / "logs")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(directory).absolute())))

    def details(self, table: DataTable) -> None:
        record = table.selected()
        if record:
            QMessageBox.information(self, "Log details", "\n".join(f"{key}: {display(item)}" for key, item in record.items()))


class StatisticsPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Statistics", "Live counters refresh approximately once per second.")
        self.table = DataTable([("Metric", "metric"), ("Value", "value")], key="metric")
        self.layout.addWidget(self.table)

    def refresh(self, snapshot: Any) -> None:
        self.table.set_records([{"metric": key.replace("_", " ").capitalize(), "value": timestamp(item) if key.endswith("start_time") else item} for key, item in value(snapshot, "statistics", {}).items() if key != "runtime_settings"])
