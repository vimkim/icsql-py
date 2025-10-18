#!/usr/bin/env python3
# repl_controller.py
import os
import socket
import pexpect
import sys
import traceback

SOCK_PATH = "/tmp/repl_controller.sock"
REPL_CMD = ["python3", "-i"]   # change to the REPL you want

def ensure_socket_removed():
    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass

def start_repl():
    # spawn REPL in a pty so it behaves like an interactive terminal
    child = pexpect.spawn(REPL_CMD[0], REPL_CMD[1:], encoding='utf-8', echo=False)
    # wait for initial prompt
    child.expect_exact('>>> ')
    return child

def handle_client(conn, child):
    try:
        with conn:
            # read until newline, strip trailing newline
            data = conn.recv(65536).decode('utf-8')
            if not data:
                return
            cmd = data.rstrip('\n')
            # send to REPL
            child.sendline(cmd)
            # wait for next prompt (this blocks until prompt appears or timeout)
            child.expect_exact('>>> ', timeout=5)
            # child.before is everything between our sendline and the prompt
            output = child.before
            # remove the command echo if present (pexpect/pty may echo the command)
            # The REPL typically echoes the command we sent as a line; drop the first line if it matches
            lines = output.splitlines()
            if lines and lines[0].strip() == cmd:
                lines = lines[1:]
            reply = "\n".join(lines).rstrip("\n") + "\n"
            conn.sendall(reply.encode('utf-8'))
    except pexpect.EOF:
        conn.sendall(b"<REPL exited>\n")
    except Exception:
        tb = traceback.format_exc()
        conn.sendall(f"<controller error>\n{tb}\n".encode('utf-8'))

def run_server():
    ensure_socket_removed()
    child = start_repl()
    print("REPL started (pid=%d). Listening on %s" % (child.pid, SOCK_PATH), file=sys.stderr)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    srv.listen(4)
    os.chmod(SOCK_PATH, 0o666)  # adjust as you like (beware security; 666 opens to all users)
    try:
        while True:
            conn, _ = srv.accept()
            handle_client(conn, child)
            # if child exited, break
            if not child.isalive():
                print("REPL exited.", file=sys.stderr)
                break
    finally:
        srv.close()
        ensure_socket_removed()

if __name__ == "__main__":
    run_server()

