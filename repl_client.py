#!/usr/bin/env python3
# repl_client.py
import socket
import sys

SOCK_PATH = "/tmp/repl_controller.sock"

def send_cmd(cmd: str):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(SOCK_PATH)
        # send command terminated by newline
        s.sendall((cmd + "\n").encode('utf-8'))
        # read reply (server sends one reply per connection)
        data = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode('utf-8')

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: repl_client.py '<python expression>'")
        sys.exit(1)
    cmd = sys.argv[1]
    print(send_cmd(cmd))

