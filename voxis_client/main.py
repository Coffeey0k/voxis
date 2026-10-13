import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)


class VoxisWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voxis Messenger")
        self.resize(900, 600)
        self.setMinimumSize(640, 420)

        root = QWidget()
        layout = QHBoxLayout(root)
        self.setCentralWidget(root)

        sidebar = QVBoxLayout()
        brand = QLabel("◉  Voxis")
        brand.setStyleSheet("font-size: 24px; font-weight: 700; padding: 12px;")
        sidebar.addWidget(brand)
        self.server = QLineEdit("http://127.0.0.1:8000")
        self.server.setPlaceholderText("Server URL")
        sidebar.addWidget(self.server)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Username")
        sidebar.addWidget(self.username)
        join = QPushButton("Join demo")
        join.clicked.connect(self.join_chat)
        sidebar.addWidget(join)
        self.status = QLabel("Not connected")
        sidebar.addWidget(self.status)
        self.chats = QListWidget()
        self.chats.addItem("General")
        sidebar.addWidget(self.chats, 1)

        main = QVBoxLayout()
        self.heading = QLabel("General")
        self.heading.setStyleSheet("font-size: 20px; font-weight: 600; padding: 12px;")
        main.addWidget(self.heading)
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setPlaceholderText("Messages will appear here")
        main.addWidget(self.history, 1)
        row = QHBoxLayout()
        self.message = QLineEdit()
        self.message.setPlaceholderText("Write a message…")
        self.message.returnPressed.connect(self.send_message)
        send = QPushButton("Send")
        send.clicked.connect(self.send_message)
        row.addWidget(self.message, 1)
        row.addWidget(send)
        main.addLayout(row)

        layout.addLayout(sidebar, 1)
        layout.addLayout(main, 3)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #151821; color: #f1f3f8; }
            QLineEdit, QTextEdit, QListWidget {
                background: #202431; border: 1px solid #363d4e;
                border-radius: 8px; padding: 8px; color: #f1f3f8;
            }
            QPushButton {
                background: #7357ff; color: white; border: none;
                border-radius: 8px; padding: 10px 14px;
            }
            QPushButton:hover { background: #6246e8; }
        """)

    def join_chat(self):
        username = self.username.text().strip()
        if not username:
            self.status.setText("Enter a username")
            return
        self.status.setText(f"Demo session: {username}")
        self.history.append("Welcome to Voxis. This UI is currently a local demo.")

    def send_message(self):
        value = self.message.text().strip()
        if not value:
            return
        username = self.username.text().strip() or "You"
        self.history.append(f"<b>{username}</b>: {value}")
        self.message.clear()


def main():
    app = QApplication(sys.argv)
    window = VoxisWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
