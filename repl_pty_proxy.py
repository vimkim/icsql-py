#!/usr/bin/env python3
import os
import pty
import signal
import sys
import selectors
import socket
import errno
import fcntl
import termios
import struct
import tty

# --- Config ---
REPL_CMD = [os.environ.get("REPL_BIN", "python3")]
UDS_PATH = os.environ.get("REPL_UDS", "/tmp/repl.sock")
LOG_INPUTS_TO = sys.stderr  # where to log intercepted input

sel = selectors.DefaultSelector()
clients = set()  # connected UDS clients (sockets)


def set_nonblock(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def get_winsize(fd):
    try:
        s = struct.pack("HHHH", 0, 0, 0, 0)
        r = fcntl.ioctl(fd, termios.TIOCGWINSZ, s)
        rows, cols, _, _ = struct.unpack("HHHH", r)
        return rows, cols
    except Exception:
        return 24, 80


def set_winsize(fd, rows, cols):
    s = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, s)
    except Exception:
        pass


def forward_winsize(sig=None, frame=None):
    if master_fd is None:
        return
    rows, cols = get_winsize(sys.stdin.fileno())
    set_winsize(master_fd, rows, cols)


def cleanup():
    try:
        os.unlink(UDS_PATH)
    except FileNotFoundError:
        pass


def accept(sock, mask):
    conn, _ = sock.accept()
    conn.setblocking(False)
    clients.add(conn)
    sel.register(conn, selectors.EVENT_READ, read_client)


def read_client(conn, mask):
    try:
        data = conn.recv(4096)
    except ConnectionResetError:
        data = b""
    if not data:
        sel.unregister(conn)
        clients.discard(conn)
        conn.close()
        return
    # Intercepted input (you can transform/filter here)
    try:
        txt = data.decode(errors="replace")
    except Exception:
        txt = repr(data)
    # Write to REPL pty
    try:
        os.write(master_fd, data)
    except OSError as e:
        if e.errno != errno.EIO:
            raise


def read_stdin(fd, mask):
    try:
        data = os.read(fd, 4096)
    except OSError as e:
        if e.errno == errno.EIO:
            data = b""
        else:
            raise
    if not data:
        # stdin closed: stop listening to it
        sel.unregister(fd)
        return
    os.write(master_fd, data)


def read_pty(fd, mask):
    try:
        data = os.read(fd, 4096)
    except OSError as e:
        # EIO → slave side closed (child exited)
        if e.errno == errno.EIO:
            data = b""
        else:
            raise
    if not data:
        # child exited
        sel.unregister(fd)
        # allow main loop to notice and exit
        return
    # Print REPL output to our stdout
    os.write(sys.stdout.fileno(), data)


def install_tty_raw_if_tty():
    if sys.stdin.isatty():
        tty.setraw(sys.stdin.fileno(), when=termios.TCSANOW)


def restore_tty_if_tty():
    # best-effort; rely on bash to restore in most cases
    pass


master_fd = None
child_pid = None


def main():
    global master_fd, child_pid
    cleanup()

    # Fork a PTY
    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        # Child: adjust PTY slave termios so \n → \r\n (fix staggered prompts)
        import termios
        for fd in (0, 1, 2):  # stdin, stdout, stderr are all on the slave
            try:
                attrs = termios.tcgetattr(fd)
                # attrs layout: [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
                iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs

                # Output post-processing + NL→CRNL
                oflag |= (termios.OPOST | termios.ONLCR)
                # Be safe: ensure OCRNL is off (CR→NL) to avoid weird loops
                if hasattr(termios, "OCRNL"):
                    oflag &= ~termios.OCRNL

                # Optional: map CR→NL on input so clients sending \r work fine
                iflag |= termios.ICRNL

                attrs[0] = iflag
                attrs[1] = oflag
                termios.tcsetattr(fd, termios.TCSANOW, attrs)
            except Exception:
                # best-effort; if this fails we still exec the REPL
                pass

        # Now exec the REPL with the fixed line discipline
        os.execvp(REPL_CMD[0], REPL_CMD)
        os._exit(1)

    # Parent
    set_nonblock(master_fd)

    # Mirror current terminal size to child
    forward_winsize()
    signal.signal(signal.SIGWINCH, forward_winsize)

    # Setup UDS server
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(UDS_PATH)
    srv.listen(16)
    srv.setblocking(False)

    # Register event sources
    sel.register(srv, selectors.EVENT_READ, accept)
    sel.register(master_fd, selectors.EVENT_READ, read_pty)
    if sys.stdin.isatty() or not os.isatty(sys.stdin.fileno()):
        set_nonblock(sys.stdin.fileno())
        sel.register(sys.stdin.fileno(), selectors.EVENT_READ, read_stdin)

    try:
        install_tty_raw_if_tty()
        # Event loop
        while True:
            if child_pid is not None:
                # poll if child exited
                pid, status = os.waitpid(child_pid, os.WNOHANG)
                if pid == child_pid:
                    break
            for key, mask in sel.select(timeout=0.2):
                callback = key.data
                callback(key.fileobj, mask)
    except KeyboardInterrupt:
        pass
    finally:
        restore_tty_if_tty()
        for c in list(clients):
            try:
                sel.unregister(c)
            except Exception:
                pass
            c.close()
        try:
            sel.unregister(master_fd)
        except Exception:
            pass
        os.close(master_fd)
        try:
            sel.unregister(srv)
        except Exception:
            pass
        srv.close()
        cleanup()


if __name__ == "__main__":
    main()
