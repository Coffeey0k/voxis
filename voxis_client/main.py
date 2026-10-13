import os
import sys
import mimetypes
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget, QFileDialog, QDialogButtonBox, QInputDialog,
)


class VoxisWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voxis Messenger")
        self.resize(1120, 720)
        self.setMinimumSize(780, 520)
        self.token = None
        self.me = None
        self.peer = None
        self.profile = {}
        self.last_ids = set()

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(16, 16, 10, 16)
        brand = QLabel("◉  Voxis")
        brand.setStyleSheet("font-size: 26px; font-weight: 800; padding: 8px;")
        sidebar.addWidget(brand)
        self.server = QLineEdit("http://127.0.0.1:8000")
        self.server.setPlaceholderText("API server URL")
        sidebar.addWidget(self.server)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        sidebar.addWidget(self.username)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        sidebar.addWidget(self.password)
        authrow = QHBoxLayout()
        login = QPushButton("Log in")
        login.clicked.connect(lambda: self.authenticate(False))
        register = QPushButton("Create account")
        register.clicked.connect(lambda: self.authenticate(True))
        authrow.addWidget(login)
        authrow.addWidget(register)
        sidebar.addLayout(authrow)
        self.status = QLabel("Not connected")
        self.status.setWordWrap(True)
        sidebar.addWidget(self.status)
        sidebar.addWidget(QLabel("CHATS"))
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("Search contacts…")
        self.chat_search.textChanged.connect(self.filter_chats)
        sidebar.addWidget(self.chat_search)
        self.chats = QListWidget()
        self.chats.currentTextChanged.connect(self.select_peer)
        sidebar.addWidget(self.chats, 1)
        prof = QPushButton("Profile & settings")
        prof.clicked.connect(self.edit_profile)
        sidebar.addWidget(prof)
        logout = QPushButton("Log out")
        logout.clicked.connect(self.logout)
        sidebar.addWidget(logout)

        main = QVBoxLayout()
        main.setContentsMargins(10, 16, 16, 16)
        header = QHBoxLayout()
        self.heading = QLabel("Your conversations")
        self.heading.setStyleSheet("font-size: 22px; font-weight: 700; padding: 8px;")
        header.addWidget(self.heading, 1)
        self.message_search = QPushButton("Search messages")
        self.message_search.clicked.connect(self.search_messages)
        header.addWidget(self.message_search)
        self.block_button = QPushButton("Block contact")
        self.block_button.clicked.connect(self.block_peer)
        header.addWidget(self.block_button)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_all)
        header.addWidget(refresh)
        main.addLayout(header)
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setPlaceholderText("Choose a contact to start chatting")
        main.addWidget(self.history, 1)
        row = QHBoxLayout()
        attach = QPushButton("＋ File")
        attach.clicked.connect(self.send_file)
        row.addWidget(attach)
        self.message = QLineEdit()
        self.message.setPlaceholderText("Write a message…")
        self.message.returnPressed.connect(self.send_message)
        row.addWidget(self.message, 1)
        send = QPushButton("Send  ➤")
        send.clicked.connect(self.send_message)
        row.addWidget(send)
        main.addLayout(row)
        layout.addLayout(sidebar, 1)
        layout.addLayout(main, 3)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0b1020; color: #edf0ff; }
            QLineEdit, QTextEdit, QListWidget {
                background: #171f35; border: 1px solid #303b5b;
                border-radius: 12px; padding: 10px; color: #edf0ff;
                selection-background-color: #6955d9;
            }
            QPushButton {
                background: #7865f5; color: white; border: none;
                border-radius: 10px; padding: 10px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #917fff; }
            QLabel { color: #edf0ff; }
        """)
        self.poll = QTimer(self)
        self.poll.setInterval(3500)
        self.poll.timeout.connect(self.load_history)
        self.poll.start()

    @property
    def base(self):
        return self.server.text().strip().rstrip("/")

    def headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request_error(self, response):
        try:
            detail = response.json().get("detail")
            if detail:
                return str(detail)
        except Exception:
            pass
        return f"HTTP {response.status_code}: {response.text[:180]}"

    def notify_error(self, message):
        QMessageBox.warning(self, "Voxis", str(message))

    def authenticate(self, register):
        name, password = self.username.text().strip(), self.password.text()
        if not name or not password:
            self.notify_error("Enter a username and password.")
            return
        try:
            r = requests.post(self.base + ("/api/register" if register else "/api/login"),
                json={"username": name, "password": password,
                      "device_id": "desktop-" + name.lower(), "platform": "desktop"}, timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            data = r.json()
            self.token, self.me = data["token"], data["username"]
            self.password.clear()
            self.status.setText(f"Connected as @{self.me}")
            self.refresh_all()
        except Exception as e:
            self.notify_error(e)

    def filter_chats(self, text):
        query = text.strip().casefold()
        for i in range(self.chats.count()):
            item = self.chats.item(i)
            item.setHidden(query not in item.text().casefold())

    def refresh_all(self):
        if not self.token:
            return
        try:
            r = requests.get(self.base + "/api/users", headers=self.headers(), timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            data = r.json().get("users", [])
            current = self.peer
            self.chats.blockSignals(True)
            self.chats.clear()
            for user in data:
                username = user.get("username", "")
                item = QListWidgetItem(username)
                avatar_url = user.get("avatar_url")
                if avatar_url:
                    try:
                        url = avatar_url if avatar_url.startswith("http") else self.base + avatar_url
                        image_response = requests.get(url, timeout=5)
                        if image_response.ok:
                            pixmap = QPixmap()
                            if pixmap.loadFromData(image_response.content):
                                item.setIcon(QIcon(pixmap.scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
                    except requests.RequestException:
                        pass
                self.chats.addItem(item)
            matches = self.chats.findItems(current or "", Qt.MatchFlag.MatchExactly)
            if matches:
                self.chats.setCurrentItem(matches[0])
            elif self.chats.count():
                self.chats.setCurrentRow(0)
            self.chats.blockSignals(False)
            self.filter_chats(self.chat_search.text())
            self.load_profile()
            self.load_history()
        except Exception as e:
            self.notify_error(e)

    def load_profile(self):
        try:
            r = requests.get(self.base + "/api/me", headers=self.headers(), timeout=10)
            if r.ok:
                self.profile = r.json()
        except requests.RequestException:
            pass

    def select_peer(self, peer):
        if not peer or peer == self.peer:
            return
        self.peer = peer
        self.last_ids.clear()
        self.heading.setText(peer)
        self.load_history()

    def load_history(self):
        if not self.token or not self.peer:
            return
        try:
            r = requests.get(self.base + "/api/messages/" + requests.utils.quote(self.peer, safe=""),
                headers=self.headers(), timeout=10)
            if not r.ok:
                return
            messages = r.json().get("messages", [])
            ids = {m.get("id") for m in messages}
            if ids == self.last_ids:
                return
            self.last_ids = ids
            chunks = []
            for m in messages:
                mine = m.get("sender") == self.me
                who = "You" if mine else m.get("sender", "Unknown")
                body = (m.get("body") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                time = str(m.get("created_at", ""))[11:16]
                attachment = m.get("attachment_id")
                if attachment:
                    body += f' <span style="color:#b9aaff">📎 {m.get("attachment_name") or "File"} · {m.get("attachment_size", 0)} bytes</span>'
                align = "right" if mine else "left"
                color = "#8774ff" if mine else "#222d49"
                chunks.append(f'<div style="margin:10px 0;text-align:{align}"><span style="background:{color};padding:10px 13px;border-radius:13px;display:inline-block;max-width:75%"><b>{who}</b><br>{body}<br><small>{time}</small></span></div>')
            self.history.setHtml("".join(chunks))
            bar = self.history.verticalScrollBar()
            bar.setValue(bar.maximum())
        except requests.RequestException:
            pass

    def search_messages(self):
        if not self.token:
            self.notify_error("Log in first.")
            return
        query, ok = QInputDialog.getText(self, "Search Voxis", "Search messages:")
        if not ok or not query.strip():
            return
        try:
            r = requests.get(self.base + "/api/search/messages",
                headers=self.headers(), params={"q": query.strip()}, timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            messages = r.json().get("messages", [])
            if not messages:
                QMessageBox.information(self, "Search", "No matching messages found.")
                return
            lines = []
            for message in messages[:100]:
                sender = message.get("sender", "?")
                body = message.get("body") or message.get("attachment_name") or "Attachment"
                lines.append(f"@{sender} · {str(message.get('created_at', ''))[:16]}\n{body}")
            QMessageBox.information(self, f"Search results ({len(messages)})", "\n\n".join(lines))
        except Exception as error:
            self.notify_error(error)

    def block_peer(self):
        if not self.token or not self.peer:
            self.notify_error("Select a contact first.")
            return
        answer = QMessageBox.question(self, "Block contact",
            f"Block @{self.peer}? They will no longer be able to exchange messages with you.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            r = requests.post(self.base + "/api/blocks", headers=self.headers(),
                json={"username": self.peer}, timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            blocked = self.peer
            self.peer = None
            self.history.clear()
            self.heading.setText("Your conversations")
            QMessageBox.information(self, "Voxis", f"@{blocked} blocked.")
        except Exception as error:
            self.notify_error(error)

    def send_message(self):
        body = self.message.text().strip()
        if not self.token or not self.peer or not body:
            return
        try:
            r = requests.post(self.base + "/api/messages", headers=self.headers(),
                json={"recipient": self.peer, "body": body}, timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            self.message.clear()
            self.load_history()
        except Exception as e:
            self.notify_error(e)

    def send_file(self):
        if not self.token or not self.peer:
            self.notify_error("Log in and select a contact first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose attachment")
        if not path:
            return
        if os.path.getsize(path) > 20 * 1024 * 1024:
            self.notify_error("Maximum file size is 20 MB.")
            return
        try:
            with open(path, "rb") as file:
                r = requests.post(self.base + "/api/attachments", headers=self.headers(),
                    files={"file": (os.path.basename(path), file, mimetypes.guess_type(path)[0] or "application/octet-stream")}, timeout=60)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            attachment_id = r.json()["id"]
            sent = requests.post(self.base + "/api/messages", headers=self.headers(),
                json={"recipient": self.peer, "body": "", "attachment_id": attachment_id}, timeout=20)
            if not sent.ok:
                raise RuntimeError(self.request_error(sent))
            self.load_history()
        except Exception as e:
            self.notify_error(e)

    def edit_profile(self):
        if not self.token:
            self.notify_error("Log in first.")
            return
        self.load_profile()
        dialog = QDialog(self)
        dialog.setWindowTitle("Profile & settings")
        form = QFormLayout(dialog)
        display = QLineEdit(self.profile.get("display_name", self.me or ""))
        bio = QTextEdit()
        bio.setPlainText(self.profile.get("bio", ""))
        bio.setMaximumHeight(100)
        form.addRow("Username", QLabel("@" + str(self.me)))
        form.addRow("Display name", display)
        form.addRow("About me", bio)
        avatar = QPushButton("Change avatar…")
        avatar.clicked.connect(lambda: self.upload_avatar())
        form.addRow("Avatar", avatar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            r = requests.patch(self.base + "/api/profile", headers={**self.headers(), "Content-Type":"application/json"},
                json={"display_name": display.text().strip(), "bio": bio.toPlainText().strip()}, timeout=15)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            self.profile = r.json()
            self.status.setText(f"Connected as @{self.me} · profile saved")
        except Exception as e:
            self.notify_error(e)

    def upload_avatar(self):
        if not self.token:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose avatar", filter="Images (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        try:
            with open(path, "rb") as file:
                r = requests.post(self.base + "/api/avatar", headers=self.headers(),
                    files={"file": (os.path.basename(path), file, mimetypes.guess_type(path)[0] or "image/jpeg")}, timeout=30)
            if not r.ok:
                raise RuntimeError(self.request_error(r))
            self.load_profile()
            self.refresh_all()
            QMessageBox.information(self, "Voxis", "Avatar updated.")
        except Exception as e:
            self.notify_error(e)

    def logout(self):
        if self.token:
            try:
                requests.post(self.base + "/api/logout", headers=self.headers(), timeout=5)
            except requests.RequestException:
                pass
        self.token, self.me, self.peer = None, None, None
        self.chats.clear()
        self.history.clear()
        self.heading.setText("Your conversations")
        self.status.setText("Not connected")


def main():
    app = QApplication(sys.argv)
    window = VoxisWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
