"""Administrative property sheets with explicit inputs and secret replacement."""
from __future__ import annotations

from typing import Any
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .widgets import DataTable, FormDialog, confirm, timestamp, value


CHANNEL_MODES = {
    "p": "Private", "s": "Secret", "m": "Moderated", "n": "No outside messages",
    "t": "Topic restricted", "i": "Invite only", "h": "Hidden", "u": "Knock",
    "f": "No formatting (client indication)", "w": "No whispers", "x": "Auditorium",
    "a": "Authenticated users only", "z": "Service indication",
}


class AccountEditor(FormDialog):
    def __init__(self, parent: QWidget, account: Any = None):
        super().__init__("Edit account" if account else "Create account", parent)
        self.account = account
        self.username = self.line("Username", value(account, "username"))
        self.display_name = self.line("Display name", value(account, "display_name"))
        self.email = self.line("Email (optional)", value(account, "email"))
        self.password = self.line("Replace password" if account else "Password", secret=True)
        self.password.setPlaceholderText("Leave blank to keep the existing password" if account else "Enter a strong password")
        self.enabled = QCheckBox("Account enabled")
        self.enabled.setChecked(value(account, "enabled", True))
        self.locked = QCheckBox("Account locked")
        self.locked.setChecked(value(account, "locked", False))
        self.form.addRow(self.enabled)
        self.form.addRow(self.locked)
        self.notes = QPlainTextEdit(str(value(account, "notes")))
        self.notes.setMaximumHeight(110)
        self.form.addRow("Notes", self.notes)

    def accept(self) -> None:
        if not self.username.text().strip() or (not self.account and not self.password.text()):
            QMessageBox.warning(self, "Account incomplete", "Enter a username and password.")
            return
        super().accept()

    def payload(self) -> dict:
        result = {"username": self.username.text().strip(), "display_name": self.display_name.text().strip(), "email": self.email.text().strip(), "notes": self.notes.toPlainText(), "enabled": self.enabled.isChecked(), "locked": self.locked.isChecked()}
        if self.account:
            result["id"] = value(self.account, "id")
        if self.password.text():
            result["password"] = self.password.text()
        self.password.clear()
        return result


class OperatorEditor(FormDialog):
    def __init__(self, parent: QWidget, accounts: Any, operator: Any = None):
        super().__init__("Operator permissions", parent)
        from OpenIRC.models.domain import ROLE_PERMISSIONS
        self.templates = ROLE_PERMISSIONS
        self.account = QComboBox()
        for account in accounts:
            self.account.addItem(value(account, "username"), value(account, "id"))
        if operator:
            self.account.setCurrentIndex(self.account.findData(value(operator, "account_id")))
            self.account.setEnabled(False)
        self.role = QComboBox()
        for role in self.templates:
            self.role.addItem(str(role).replace("_", " ").title(), role)
        self.enabled = QCheckBox("Operator grant enabled")
        self.enabled.setChecked(value(operator, "enabled", True))
        self.form.addRow("Account", self.account)
        self.form.addRow("Role template", self.role)
        self.form.addRow(self.enabled)
        group = QGroupBox("Explicit permissions")
        grid = QGridLayout(group)
        self.permissions = {}
        names = sorted(set().union(*self.templates.values()))
        for index, name in enumerate(names):
            check = QCheckBox(name.replace("_", " ").capitalize())
            grid.addWidget(check, index // 2, index % 2)
            self.permissions[name] = check
        self.form.addRow(group)
        self.role.currentIndexChanged.connect(self.apply_template)
        if operator:
            self.role.setCurrentIndex(max(0, self.role.findData(value(operator, "role"))))
            current = value(operator, "permissions", ())
            for name, check in self.permissions.items():
                check.setChecked(name in current)
        else:
            self.apply_template()

    def apply_template(self) -> None:
        permissions = self.templates.get(self.role.currentData(), ())
        for name, check in self.permissions.items():
            check.setChecked(name in permissions)

    def payload(self) -> dict:
        return {"account_id": self.account.currentData(), "role": self.role.currentData(), "enabled": self.enabled.isChecked(), "permissions": [name for name, check in self.permissions.items() if check.isChecked()]}


class BanEditor(FormDialog):
    def __init__(self, parent: QWidget, ban: Any = None, mask: str = ""):
        super().__init__("Server ban", parent)
        self.ban = ban
        self.kind = QComboBox()
        self.kind.addItems(["host", "ip", "cidr", "nick", "user", "account", "mask"])
        self.kind.setCurrentText(value(ban, "type", "host"))
        self.form.addRow("Match", self.kind)
        self.mask = self.line("Mask", value(ban, "mask", mask))
        self.reason = self.line("Reason", value(ban, "reason"))
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 5256000)
        self.minutes.setSpecialValueText("Permanent")
        expiry = value(ban, "expires_at", None)
        if expiry:
            self.minutes.setValue(max(1, int((expiry - time.time()) / 60)))
        self.form.addRow("Expires after (minutes)", self.minutes)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(value(ban, "enabled", True))
        self.disconnect = QCheckBox("Disconnect matching users now")
        self.form.addRow(self.enabled)
        self.form.addRow(self.disconnect)

    def payload(self) -> dict:
        result = {"type": self.kind.currentText(), "mask": self.mask.text().strip(), "reason": self.reason.text().strip(), "expires_at": time.time() + self.minutes.value() * 60 if self.minutes.value() else None, "enabled": self.enabled.isChecked(), "disconnect": self.disconnect.isChecked()}
        if self.ban:
            result["id"] = value(self.ban, "id")
        return result


class AccessEditor(FormDialog):
    def __init__(self, parent: QWidget, object_name: str):
        super().__init__(f"Add access entry · {object_name}", parent)
        self.object_name = object_name
        self.level = QComboBox()
        self.level.addItems(["DENY", "GRANT", "VOICE", "HOST", "OWNER"])
        self.form.addRow("Level", self.level)
        self.mask = self.line("Mask")
        self.mask.setPlaceholderText("nickname!username@host")
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 5256000)
        self.minutes.setSpecialValueText("Permanent")
        self.form.addRow("Expires after (minutes)", self.minutes)
        self.reason = self.line("Reason")

    def payload(self) -> dict:
        return {"object": self.object_name, "operation": "ADD", "level": self.level.currentText(), "mask": self.mask.text().strip(), "timeout": self.minutes.value(), "reason": self.reason.text()}


class ChannelEditor(QDialog):
    def __init__(self, parent: QWidget, channel: Any = None, accounts: Any = (), controller: Any = None, tab: str = "General"):
        super().__init__(parent)
        self.channel = channel
        self.controller = controller
        self.setWindowTitle("Channel properties" if channel else "Create channel")
        self.resize(800, 620)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.forms = {}
        for title in ["General", "Properties", "Modes", "Members", "Access", "Security"]:
            page = QWidget()
            form = QFormLayout(page)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            self.tabs.addTab(page, title)
            self.forms[title] = form
        self.tabs.setCurrentIndex(["General", "Properties", "Modes", "Members", "Access", "Security"].index(tab))
        form = self.forms["General"]
        self.name = QLineEdit(value(channel, "name"))
        self.name.setPlaceholderText("#general")
        self.name.setReadOnly(bool(channel))
        form.addRow("Channel name", self.name)
        self.topic = QLineEdit(value(channel, "topic"))
        self.subject = QLineEdit(value(channel, "subject"))
        self.language = QLineEdit(value(channel, "language", "en-US"))
        self.registered = QCheckBox("Keep this channel after restart")
        self.registered.setChecked(value(channel, "registered", True))
        self.founder = QComboBox()
        self.founder.addItem("No founder account", None)
        for account in accounts:
            self.founder.addItem(value(account, "username"), value(account, "id"))
        self.founder.setCurrentIndex(max(0, self.founder.findData(value(channel, "founder_id", None))))
        self.limit = QSpinBox()
        self.limit.setRange(0, 1000000)
        self.limit.setSpecialValueText("Unlimited")
        self.limit.setValue(value(channel, "limit", 0))
        for label, widget in [("Topic", self.topic), ("Subject", self.subject), ("Language", self.language), ("Registration", self.registered), ("Founder", self.founder), ("User limit", self.limit)]:
            form.addRow(label, widget)
        form.addRow("Created", QLabel(timestamp(value(channel, "created_at", None))))
        form.addRow("Object identifier", QLabel(str(value(channel, "oid", "Assigned on creation"))))
        self.properties = {}
        for name in ["ONJOIN", "ONPART", "CLIENT", "CLIENTGUID", "LAG"]:
            text = str(value(value(channel, "properties", {}), name))
            if name in {"ONJOIN", "ONPART"}:
                field = QPlainTextEdit(text.replace("\\n", "\n"))
                field.setMaximumHeight(95)
                field.setPlaceholderText("Private message to the joining user" if name == "ONJOIN" else "Private message to the departing user")
            else:
                field = QLineEdit(text)
            self.properties[name] = field
            self.forms["Properties"].addRow(name, field)
        self.forms["Properties"].addRow("ACCOUNT (read-only)", QLabel(str(value(channel, "founder_id", None) or "No founder account")))
        self.modes = {}
        active = value(channel, "modes", "nt")
        for mode, description in CHANNEL_MODES.items():
            check = QCheckBox(f"{description} (+{mode})")
            check.setChecked(mode in active)
            self.forms["Modes"].addRow(check)
            self.modes[mode] = check
        clone = QCheckBox("Automatic cloning (+d / +e) — planned")
        clone.setEnabled(False)
        self.forms["Modes"].addRow(clone)
        self.keys = {}
        self.reset_keys = {}
        info = QLabel("Keys cannot be viewed. Enter a replacement, or select Reset to remove a key.")
        info.setWordWrap(True)
        self.forms["Security"].addRow(info)
        for name in ["MEMBERKEY", "HOSTKEY", "OWNERKEY"]:
            row = QHBoxLayout()
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText("Configured · unchanged" if name in value(channel, "secrets_set", ()) else "No key configured")
            clear = QCheckBox("Reset")
            clear.toggled.connect(lambda checked, edit=field: edit.setEnabled(not checked))
            row.addWidget(field)
            row.addWidget(clear)
            self.forms["Security"].addRow(name.replace("KEY", " key").capitalize(), row)
            self.keys[name] = field
            self.reset_keys[name] = clear
        self.forms["Security"].addRow(QLabel("Authenticated-only access is controlled by +a on the Modes tab."))
        self.members = DataTable([("Nickname", "nick"), ("Account", "account"), ("Role", "role"), ("Joined", lambda r: timestamp(value(r, "joined_at"))), ("Idle", "idle"), ("Host", "host")], key="session_id")
        self.members.set_records(value(channel, "members", ()))
        self.forms["Members"].addRow(self.members)
        member_bar = QHBoxLayout()
        self.role = QComboBox()
        self.role.addItems(["Owner", "Host", "Voice", "Member"])
        member_bar.addWidget(self.role)
        apply_role = QPushButton("Set role")
        apply_role.clicked.connect(self.set_role)
        apply_role.setEnabled(False)
        self.members.itemSelectionChanged.connect(lambda: apply_role.setEnabled(self.members.selected() is not None))
        member_bar.addWidget(apply_role)
        for label, callback in [("Kick", self.kick), ("Ban", self.ban_member), ("View user", self.view_member)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setEnabled(False)
            self.members.itemSelectionChanged.connect(lambda b=button: b.setEnabled(self.members.selected() is not None))
            member_bar.addWidget(button)
        self.forms["Members"].addRow(member_bar)
        self.access = DataTable([("Level", "level"), ("Mask", "mask"), ("Reason", "reason"), ("Added by", "created_by"), ("Created", lambda r: timestamp(value(r, "created_at"))), ("Expires", lambda r: timestamp(value(r, "expires_at")))])
        self.access.set_records(value(channel, "access", ()))
        self.forms["Access"].addRow(self.access)
        access_bar = QHBoxLayout()
        for label, callback in [("Add entry", self.add_access), ("Remove entry", self.remove_access), ("Clear entries", self.clear_access)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            access_bar.addWidget(button)
            button.setEnabled(channel is not None)
            if label == "Remove entry":
                button.setEnabled(False)
                self.access.itemSelectionChanged.connect(lambda b=button: b.setEnabled(self.access.selected() is not None))
        self.forms["Access"].addRow(access_bar)
        if not channel:
            self.forms["Access"].addRow(QLabel("Create the channel before editing its access list."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        if not self.name.text().strip().startswith(("#", "&")):
            QMessageBox.warning(self, "Invalid channel", "A channel name must start with # or &.")
            return
        super().accept()

    def payload(self) -> dict:
        modes = "".join(mode for mode, check in self.modes.items() if check.isChecked())
        if self.limit.value():
            modes += "l"
        if "MEMBERKEY" in value(self.channel, "secrets_set", ()) and not self.reset_keys["MEMBERKEY"].isChecked():
            modes += "k"
        properties = {name: field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text() for name, field in self.properties.items()}
        secrets = {name: "" if self.reset_keys[name].isChecked() else field.text() for name, field in self.keys.items() if self.reset_keys[name].isChecked() or field.text()}
        result = {"name": self.name.text().strip(), "topic": self.topic.text(), "subject": self.subject.text(), "language": self.language.text(), "registered": self.registered.isChecked(), "founder_id": self.founder.currentData(), "limit": self.limit.value(), "modes": modes, "properties": properties, "secrets": secrets}
        if self.channel:
            result["id"] = value(self.channel, "id")
        for field in self.keys.values():
            field.clear()
        return result

    def refresh(self, snapshot: Any) -> None:
        if not self.channel:
            return
        for channel in value(snapshot, "channels", ()):
            if value(channel, "id") == value(self.channel, "id"):
                self.channel = channel
                self.members.set_records(value(channel, "members", ()))
                self.access.set_records(value(channel, "access", ()))
                break

    def set_role(self) -> None:
        member = self.members.selected()
        if member and self.controller:
            role = self.role.currentText().lower()
            if confirm(self, "Change channel role", f"Set {value(member, 'nick')} to {role} in {value(self.channel, 'name')}?"):
                self.controller.command("channel.role", {"id": value(self.channel, "id"), "session_id": value(member, "session_id"), "role": role})

    def kick(self) -> None:
        member = self.members.selected()
        if member and self.controller and confirm(self, "Kick member", f"Remove {value(member, 'nick')} from this channel?"):
            self.controller.command("channel.kick", {"id": value(self.channel, "id"), "session_id": value(member, "session_id"), "reason": "Removed by administrator"})

    def ban_member(self) -> None:
        member = self.members.selected()
        if member and self.controller:
            editor = AccessEditor(self, value(self.channel, "name"))
            editor.mask.setText(f"*!*@{value(member, 'host')}")
            if editor.exec():
                self.controller.command("access.change", editor.payload())

    def view_member(self) -> None:
        member = self.members.selected()
        if member and self.controller:
            for record in value(self.controller.snapshot, "connections", ()):
                if value(record, "id") == value(member, "session_id"):
                    UserDetails(self, record).exec()
                    return
            QMessageBox.information(self, "User disconnected", "This user is no longer connected.")

    def add_access(self) -> None:
        editor = AccessEditor(self, value(self.channel, "name"))
        if editor.exec() and self.controller:
            self.controller.command("access.change", editor.payload())

    def remove_access(self) -> None:
        entry = self.access.selected()
        if entry and self.controller and confirm(self, "Remove access entry", f"Remove {value(entry, 'level')} {value(entry, 'mask')}?"):
            self.controller.command("access.change", {"object": value(self.channel, "name"), "operation": "DELETE", "level": value(entry, "level"), "mask": value(entry, "mask")})

    def clear_access(self) -> None:
        if self.controller and confirm(self, "Clear access", "Remove every access entry from this channel?"):
            self.controller.command("access.change", {"object": value(self.channel, "name"), "operation": "CLEAR"})


class UserDetails(FormDialog):
    def __init__(self, parent: QWidget, connection: Any):
        super().__init__(f"User details · {value(connection, 'nick')}", parent)
        self.buttons.setStandardButtons(QDialogButtonBox.StandardButton.Close)
        for label, field in [("Nickname", "nick"), ("Account", "account"), ("Username", "username"), ("Real name", "realname"), ("Remote IP", "ip"), ("Displayed host", "host"), ("Transport", "transport"), ("IRCX", "ircx"), ("Operator", "operator"), ("User modes", "modes"), ("Channels", "channels"), ("Bytes received", "bytes_in"), ("Bytes sent", "bytes_out")]:
            from .widgets import display
            text = QLabel(display(value(connection, field)))
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setWordWrap(True)
            self.form.addRow(label, text)
        self.form.addRow("Connected", QLabel(timestamp(value(connection, "connected_at"))))
        self.form.addRow("Idle (seconds)", QLabel(str(value(connection, "idle"))))


class BroadcastDialog(FormDialog):
    def __init__(self, parent: QWidget, channels: Any):
        super().__init__("Broadcast notice", parent)
        self.target = QComboBox()
        self.target.addItem("Select a target…", None)
        self.target.addItem("All connected users", "all")
        self.target.addItem("Operators (WALLOPS)", "operators")
        for channel in channels:
            name = value(channel, "name")
            self.target.addItem(name, name)
        self.form.addRow("Send to", self.target)
        self.message = QPlainTextEdit()
        self.message.setMaximumHeight(160)
        self.form.addRow("Message", self.message)

    def accept(self) -> None:
        if not self.target.currentData() or not self.message.toPlainText().strip():
            QMessageBox.warning(self, "Broadcast incomplete", "Select a target and enter a message.")
            return
        super().accept()

    def payload(self) -> dict:
        return {"target": self.target.currentData(), "message": self.message.toPlainText()}
