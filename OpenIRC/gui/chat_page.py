"""Operator chat, rendered solely from its own protocol session output."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from .widgets import Page, value


def split_line(line: str) -> tuple[str, str, list[str]]:
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    before, marker, trailing = line.partition(" :")
    words = before.split()
    if marker:
        words.append(trailing)
    return prefix, words[0].upper() if words else "", words[1:]


class ChatRoom(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter()
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMaximumBlockCount(1500)
        self.members = QListWidget()
        self.members.setMaximumWidth(200)
        self.member_names: dict[str, str] = {}
        self.pending_names: dict[str, str] | None = None
        splitter.addWidget(self.transcript)
        splitter.addWidget(self.members)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(splitter)

    def append(self, text: str) -> None:
        self.transcript.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def show_members(self) -> None:
        self.members.clear()
        self.members.addItems(sorted(self.member_names.values(), key=lambda name: name.lstrip(".@+~&").casefold()))


class ChatPage(Page):
    def __init__(self, controller: Any):
        super().__init__("Chat", "Sign in with an operator account and choose a nickname to join conversations.")
        self.controller = controller
        self.session_id: str | None = None
        self.nickname = ""
        self.rooms: dict[str, ChatRoom] = {}
        self.poll_pending = False
        self.connect_pending = False
        self.pending_join = ""
        self.running = False
        auth = QGroupBox("Operator sign-in")
        form = QHBoxLayout(auth)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Operator account")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.nick = QLineEdit()
        self.nick.setPlaceholderText("Chat nickname")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_chat)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_chat)
        for widget in [self.username, self.password, self.nick, self.connect_button, self.disconnect_button]:
            form.addWidget(widget)
        self.layout.addWidget(auth)
        self.status = QLabel("Start the server, then connect with an operator account.")
        self.layout.addWidget(self.status)
        bar = QHBoxLayout()
        self.channel = QLineEdit()
        self.channel.setPlaceholderText("Channel to join, for example #general")
        self.join_button = QPushButton("Join channel")
        self.join_button.clicked.connect(lambda: self.join_channel(self.channel.text().strip()))
        self.part_button = QPushButton("Leave channel")
        self.part_button.clicked.connect(self.part_channel)
        bar.addWidget(self.channel)
        bar.addWidget(self.join_button)
        bar.addWidget(self.part_button)
        self.layout.addLayout(bar)
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)
        self.room("Server")
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message, or /join, /part, /msg, /nick, /me, /raw…")
        self.input.returnPressed.connect(self.send_input)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send_input)
        row.addWidget(self.input)
        row.addWidget(self.send_button)
        self.layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.update_controls()

    def room(self, name: str) -> ChatRoom:
        existing = next((room for key, room in self.rooms.items() if key.casefold() == name.casefold()), None)
        if existing:
            return existing
        if len(self.rooms) >= 100:
            return self.rooms["Server"]
        result = ChatRoom()
        if name == "Server" or not name.startswith(("#", "&")):
            result.members.hide()
        self.rooms[name] = result
        self.tabs.addTab(result, name)
        return result

    def update_controls(self) -> None:
        connected = self.session_id is not None
        self.connect_button.setEnabled(self.running and not connected and not self.connect_pending)
        self.disconnect_button.setEnabled(connected)
        for widget in [self.username, self.password, self.nick]:
            widget.setEnabled(not connected and not self.connect_pending)
        for widget in [self.join_button, self.part_button, self.input, self.send_button, self.channel]:
            widget.setEnabled(connected)

    def refresh(self, snapshot: Any) -> None:
        self.running = str(value(snapshot, "status", "stopped")).lower() == "running"
        if not self.running and self.session_id:
            self.disconnected("Server stopped. Connect again after restarting.")
        self.update_controls()

    def connect_chat(self) -> None:
        if not all([self.username.text().strip(), self.password.text(), self.nick.text().strip()]):
            QMessageBox.warning(self, "Sign-in incomplete", "Enter an operator account, password, and independent chat nickname.")
            return
        self.connect_pending = True
        self.nickname = self.nick.text().strip()
        payload = {"username": self.username.text().strip(), "password": self.password.text(), "nickname": self.nickname}
        self.password.clear()
        self.update_controls()
        self.status.setText("Connecting…")
        self.controller.command("chat.connect", payload, callback=self.connected, failure=self.connect_failed)

    def connect_failed(self, message: str) -> None:
        self.connect_pending = False
        self.status.setText(message)
        self.update_controls()

    def connected(self, result: Any) -> None:
        self.connect_pending = False
        self.session_id = str(result if isinstance(result, str) else value(result, "id"))
        self.status.setText(f"Connected as {self.nickname} · Local transport")
        self.update_controls()
        if self.pending_join:
            self.join_channel(self.pending_join)
            self.pending_join = ""

    def disconnect_chat(self) -> None:
        if self.session_id:
            self.controller.command("chat.disconnect", {"id": self.session_id})
        self.disconnected("Disconnected. Passwords are never saved.")

    def disconnected(self, reason: str) -> None:
        self.session_id = None
        self.password.clear()
        self.status.setText(reason)
        self.room("Server").append(reason)
        for room in self.rooms.values():
            room.member_names.clear()
            room.pending_names = None
            room.show_members()
        self.update_controls()

    def poll(self) -> None:
        if self.session_id and not self.poll_pending:
            self.poll_pending = True
            self.controller.command("chat.poll", {"id": self.session_id}, callback=self.received, failure=self.poll_failed, refresh=False)

    def poll_failed(self, message: str) -> None:
        self.poll_pending = False
        self.disconnected(message)

    def received(self, result: Any) -> None:
        self.poll_pending = False
        for line in result or ():
            self.handle_line(line)

    def send_line(self, line: str) -> None:
        if self.session_id:
            self.controller.command("chat.send", {"id": self.session_id, "line": line}, refresh=False)

    def join_channel(self, channel: str) -> None:
        if not channel:
            return
        if not self.session_id:
            self.pending_join = channel
            self.channel.setText(channel)
            self.status.setText(f"Sign in to join {channel}.")
            self.username.setFocus()
            return
        self.send_line(f"JOIN {channel}")

    def part_channel(self) -> None:
        target = self.tabs.tabText(self.tabs.currentIndex())
        if target.startswith(("#", "&")):
            self.send_line(f"PART {target} :Leaving")

    def send_input(self) -> None:
        text = self.input.text().strip()
        if not text or not self.session_id:
            return
        self.input.clear()
        target = self.tabs.tabText(self.tabs.currentIndex())
        if text.startswith("/"):
            command, _, argument = text[1:].partition(" ")
            command = command.lower()
            if command == "join":
                self.join_channel(argument)
            elif command == "part":
                self.send_line(f"PART {argument or target}")
            elif command == "nick":
                self.send_line(f"NICK {argument}")
            elif command == "me" and target != "Server":
                self.send_line(f"PRIVMSG {target} :\x01ACTION {argument}\x01")
                self.room(target).append(f"* {self.nickname} {argument}")
            elif command in {"msg", "notice"}:
                recipient, _, message = argument.partition(" ")
                if recipient and message:
                    self.send_line(f"{'PRIVMSG' if command == 'msg' else 'NOTICE'} {recipient} :{message}")
                    self.room(recipient).append(f"<{self.nickname}> {message}")
            elif command == "raw":
                self.send_line(argument)
            else:
                self.room("Server").append("Supported commands: /join, /part, /msg, /notice, /nick, /me, /raw.")
        elif target != "Server":
            self.send_line(f"PRIVMSG {target} :{text}")
            self.room(target).append(f"<{self.nickname}> {text}")
        else:
            self.room("Server").append("Join a channel or use /msg nickname message.")

    def handle_line(self, line: str) -> None:
        prefix, command, params = split_line(line)
        sender = prefix.split("!", 1)[0]
        settings = value(self.controller.snapshot, "statistics", {}).get("runtime_settings", value(self.controller.snapshot, "settings", {}))
        if settings.get("unicode_mode") == "historical":
            from OpenIRC.protocol.unicode import incoming_name
            def decoded(name):
                try:
                    return incoming_name(name, historical=True)
                except ValueError:
                    return name
            sender = decoded(sender)
            params = list(params)
            if command in {"JOIN", "CREATE", "PART", "KICK", "MODE", "TOPIC", "PRIVMSG", "NOTICE", "NICK", "WHISPER"} and params:
                params[0] = decoded(params[0])
            if command == "KICK" and len(params) > 1:
                params[1] = decoded(params[1])
            if command in {"331", "332", "333", "366"} and len(params) > 1:
                params[1] = decoded(params[1])
            if command == "353" and len(params) >= 4:
                params[-2] = decoded(params[-2])
                names = []
                for name in params[-1].split():
                    bare = name.lstrip(".@+~&")
                    names.append(name[:len(name) - len(bare)] + decoded(bare))
                params[-1] = " ".join(names)
        own = sender.casefold() == self.nickname.casefold()
        if command == "PING":
            self.send_line(f"PONG :{params[-1] if params else ''}")
            return
        if command in {"PRIVMSG", "NOTICE"} and len(params) >= 2:
            target = params[0] if params[0].startswith(("#", "&")) else sender if command == "PRIVMSG" else "Server"
            message = params[-1]
            if message.startswith("\x01ACTION ") and message.endswith("\x01"):
                text = f"* {sender} {message[8:-1]}"
            else:
                text = f"<{sender}> {message}" if command == "PRIVMSG" else f"Notice from {sender}: {message}"
            self.room(target).append(text)
        elif command == "353" and len(params) >= 4:
            room = self.room(params[-2])
            if room.pending_names is None:
                room.pending_names = {}
            for name in params[-1].split():
                room.pending_names[name.lstrip(".@+~&").casefold()] = name
        elif command == "366" and len(params) >= 2:
            room = self.room(params[1])
            if room.pending_names is not None:
                room.member_names = room.pending_names
                room.pending_names = None
                room.show_members()
        elif command in {"JOIN", "CREATE"} and params:
            room = self.room(params[0])
            room.member_names[sender.casefold()] = sender
            room.show_members()
            room.append(f"{sender} joined")
            if own:
                self.tabs.setCurrentWidget(room)
                self.send_line(f"NAMES {params[0]}")
        elif command in {"PART", "KICK"} and params:
            room = self.room(params[0])
            departed = params[1] if command == "KICK" and len(params) > 1 else sender
            room.member_names.pop(departed.casefold(), None)
            room.show_members()
            room.append(f"{departed} {'was kicked' if command == 'KICK' else 'left'}" + (f": {params[-1]}" if len(params) > (2 if command == "KICK" else 1) else ""))
            if departed.casefold() == self.nickname.casefold():
                room.member_names.clear()
                room.show_members()
        elif command == "QUIT":
            for room in self.rooms.values():
                if room.member_names.pop(sender.casefold(), None):
                    room.append(f"{sender} quit: {params[-1] if params else ''}")
                    room.show_members()
        elif command == "NICK" and params:
            for room in self.rooms.values():
                previous = room.member_names.pop(sender.casefold(), None)
                if previous:
                    status = previous[:len(previous) - len(previous.lstrip(".@+~&"))]
                    room.member_names[params[0].casefold()] = status + params[0]
                    room.append(f"{sender} is now {params[0]}")
                    room.show_members()
            if own:
                self.nickname = params[0]
                self.status.setText(f"Connected as {self.nickname} · Local transport")
        elif command == "MODE" and params:
            room = self.room(params[0] if params[0].startswith(("#", "&")) else "Server")
            room.append(f"{sender} set modes: {' '.join(params[1:])}")
            if params[0].startswith(("#", "&")):
                self.send_line(f"NAMES {params[0]}")
        elif command == "TOPIC" and len(params) >= 2:
            self.room(params[0]).append(f"{sender} changed the topic: {params[-1]}")
        elif command == "ERROR":
            self.disconnected(params[-1] if params else "Disconnected")
        elif command in {"332", "331"} and len(params) >= 2:
            self.room(params[1]).append("Topic: " + params[-1])
        elif command not in {"005", "333"}:
            self.room("Server").append(" ".join(params[1:] if command.isdigit() else params) or line)
