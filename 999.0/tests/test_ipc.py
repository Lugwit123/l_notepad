from PySide6 import QtNetwork
import json

socket = QtNetwork.QLocalSocket()
socket.connectToServer("l_notepad_pc_ipc")
if socket.waitForConnected(1000):
    payload = json.dumps({"files": [r"C:\Windows\System32\drivers\etc\hosts"]}, ensure_ascii=False).encode("utf-8")
    socket.write(payload)
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()