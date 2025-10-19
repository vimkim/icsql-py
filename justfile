send:
    printf 'print(2+2)\n' | socat - UNIX-CONNECT:/tmp/repl.sock

send-csql:
    printf 'show tables;\n' o> /tmp/pyrepl.in

run:
    uv run repl_pty_proxy.py

pyrepl:
    uv run pyrepl.py

