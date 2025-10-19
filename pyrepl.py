#!/usr/bin/env python3
import os
import sys
import pty
import selectors
import errno
import stat
import termios
import tty
import fcntl
import struct
import signal

FIFO_PATH = os.environ.get("PYREPL_FIFO", "/tmp/pyrepl.in")
TIOCGWINSZ = getattr(termios, 'TIOCGWINSZ', 0x5413)
TIOCSWINSZ = getattr(termios, 'TIOCSWINSZ', 0x5414)


def ensure_fifo(path):
    try:
        st = os.stat(path)
        if not stat.S_ISFIFO(st.st_mode):
            raise RuntimeError(f"{path} exists and is not a FIFO")
    except FileNotFoundError:
        os.mkfifo(path, 0o666)


def open_fifo_rw(path):
    # Open a read fd (nonblocking) and a write fd to keep the FIFO "open"
    r = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    w = os.open(path, os.O_WRONLY | os.O_NONBLOCK)  # keepalive writer
    return r, w


def reopen_fifo_reader(path):
    while True:
        try:
            return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno in (errno.ENXIO, errno.ENOENT):
                # No writer yet or race; try again shortly.
                import time
                time.sleep(0.05)
                continue
            raise


def set_nonblocking(fd, enable=True):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if enable:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)


def get_winsz(fd):
    try:
        h, w, ph, pw = struct.unpack(
            "HHHH", fcntl.ioctl(fd, TIOCGWINSZ, b"\0"*8))
        return h, w, ph, pw
    except Exception:
        return 24, 80, 0, 0


def set_winsz(fd, sz):
    if not TIOCSWINSZ:
        return
    h, w, ph, pw = sz
    fcntl.ioctl(fd, TIOCSWINSZ, struct.pack("HHHH", h, w, ph, pw))


def main():
    ensure_fifo(FIFO_PATH)

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: attach to PTY as controlling tty and exec python
        # os.execvp("python3", ["python3"])
        os.execvp("csql", ["csql", "-Sudba", "testdb"])
        raise SystemExit(1)

    # Parent: bridge FIFO -> PTY (stdin) and PTY -> stdout
    fifo_r, fifo_w_keepalive = open_fifo_rw(FIFO_PATH)

    stdin_fd = sys.stdin.fileno()
    old_tio = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    set_nonblocking(stdin_fd, True)

    set_winsz(master_fd, get_winsz(stdin_fd))

    def on_winch(signum, frame):
        set_winsz(master_fd, get_winsz(stdin_fd))
    signal.signal(signal.SIGWINCH, on_winch)

    def on_sigint(signum, frame):
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    signal.signal(signal.SIGINT, on_sigint)

    sel = selectors.DefaultSelector()
    sel.register(master_fd, selectors.EVENT_READ, data="pty")
    sel.register(fifo_r, selectors.EVENT_READ, data="fifo")
    sel.register(stdin_fd, selectors.EVENT_READ, data="stdin")

    out = sys.stdout.buffer
    try:
        while True:
            for key, _ in sel.select():
                src = key.data
                if src == "pty":
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as e:
                        if e.errno == errno.EIO:
                            return  # child exited
                        raise
                    if not data:
                        return
                    out.write(data)
                    out.flush()

                elif src == "fifo":
                    try:
                        chunk = os.read(fifo_r, 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        # Writer closed; reopen reader to accept next writer
                        sel.unregister(fifo_r)
                        os.close(fifo_r)
                        fifo_r = reopen_fifo_reader(FIFO_PATH)
                        sel.register(fifo_r, selectors.EVENT_READ, data="fifo")
                        continue
                    os.write(master_fd, chunk)
                else:  # "stdin" -> keystrokes typed in this terminal
                    try:
                        chunk = os.read(stdin_fd, 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return  # stdin closed
                    os.write(master_fd, chunk)

    finally:
        # Restore terminal and clean up
        try: termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tio)
        except Exception: pass
        set_nonblocking(stdin_fd, False)
        for fd in (master_fd, fifo_r, fifo_w_keepalive):
            try: sel.unregister(fd)
            except Exception: pass
        for fd in (master_fd, fifo_r, fifo_w_keepalive):
            try: os.close(fd)
            except Exception: pass

if __name__ == "__main__":
    main()
