"""Desktopové okno pro TimeTrack stopky (vždy navrchu).

Malé okno, které jen zobrazí /widget z běžící TimeTrack appky.

    pip install -r requirements-desktop.txt
    python3 desktop_widget.py

pywebview potřebuje GTK nebo Qt backend:
    Debian/Ubuntu:  sudo apt install python3-gi gir1.2-webkit2-4.1
    nebo:           pip install pywebview[qt]

Adresu lze přepsat proměnnou TIMETRACK_URL.
"""
import os

import webview

URL = os.environ.get("TIMETRACK_URL", "http://localhost:8731/widget")

if __name__ == "__main__":
    webview.create_window(
        "TimeTrack ⏱",
        URL,
        width=380,
        height=480,
        min_size=(300, 300),
        on_top=True,
        resizable=True,
    )
    webview.start(private_mode=False)  # keep the login cookie between launches
