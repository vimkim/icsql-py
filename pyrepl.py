#!/usr/bin/env python3
import os, sys, pty, selectors, errno, stat

FIFO_PATH = os.environ.get("PYREPL_FIFO", "/tmp/pyrepl.in")

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
                import time; time.sleep(0.05)
                continue
            raise

def main():
    ensure_fifo(FIFO_PATH)

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: attach to PTY as controlling tty and exec python
        os.execvp("python3", ["python3", "-q"])
        raise SystemExit(1)

    # Parent: bridge FIFO -> PTY (stdin) and PTY -> stdout
    fifo_r, fifo_w_keepalive = open_fifo_rw(FIFO_PATH)

    sel = selectors.DefaultSelector()
    sel.register(master_fd, selectors.EVENT_READ, data="pty")
    sel.register(fifo_r, selectors.EVENT_READ, data="fifo")

    out = sys.stdout.buffer
    try:
        while True:
            for key, _ in sel.select():
                if key.data == "pty":
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as e:
                        if e.errno in (errno.EIO,):
                            # PTY probably closed because child exited.
                            return
                        raise
                    if not data:
                        return
                    out.write(data)
                    out.flush()
                else:
                    # Read from FIFO and write to PTY
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
                    # Ensure newline if someone forgot it? Your call:
                    # if not chunk.endswith(b"\n"): chunk += b"\n"
                    os.write(master_fd, chunk)
    finally:
        try: sel.unregister(master_fd)
        except Exception: pass
        try: sel.unregister(fifo_r)
        except Exception: pass
        for fd in (master_fd, fifo_r, fifo_w_keepalive):
            try: os.close(fd)
            except Exception: pass

if __name__ == "__main__":
    main()

