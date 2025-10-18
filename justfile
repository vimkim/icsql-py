send:
    printf 'print(2+2)\n' | socat - UNIX-CONNECT:/tmp/repl.sock

run:
    uv run repl_pty_proxy.py
