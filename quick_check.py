#!/usr/bin/env python3
# quick_check.py
import pexpect
import sys

# Unique prompts so we can reliably match them
PS1 = "<<PY>>>"
PS2 = "<<PY..>> "

def main():
    # 1) Spawn Python REPL in a PTY
    child = pexpect.spawn("python3", ["-q", "-i"], encoding="utf-8")
    child.delaybeforesend = 0
    child.setecho(False)            # <- crucial: don't echo what we type

    # 2) Wait for the initial default prompt, then install our own prompts
    child.expect_exact(">>> ", timeout=10)
    child.sendline(f"import sys; sys.ps1='{PS1}'; sys.ps2='{PS2}'")
    child.expect_exact(PS1, timeout=10)  # now we're synced on our prompt

    def run(cmd: str, timeout: float = 10.0):
        """Send one command, wait for prompt, print the REPL's output."""
        child.sendline(cmd)
        child.expect_exact(PS1, timeout=timeout)
        out = child.before  # everything printed between our send and the next prompt

        # With echo disabled, out should NOT contain the input line.
        # Still, be defensive in case some env forces echo:
        lines = out.splitlines()
        if lines and lines[0].strip() == cmd.strip():
            lines = lines[1:]
        print("\n".join(lines))

    run("2+2")                           # -> 4
    run("import math; math.sqrt(2)")     # -> 1.4142135623730951

    # polite exit
    try:
        child.sendcontrol("d")           # Ctrl-D
    except Exception:
        pass

if __name__ == "__main__":
    sys.exit(main())
