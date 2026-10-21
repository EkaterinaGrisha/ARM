"""Run a command under a memory limit: ``python -m arm_web.sandbox --memory-mb N -- COMMAND ...``.

The limit is set here and the command replaces this process, so it and its children (Tesseract) inherit it.
Setting it in the job runner itself is unsafe: ``preexec_fn`` is not thread-safe and the API runs jobs in threads.
Linux enforces ``RLIMIT_AS``; macOS accepts it but may not enforce it, which is fine for development.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arm_web.sandbox")
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command")
    limit = args.memory_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError) as exc:
        print(f"Лимит памяти не установлен: {exc}", file=sys.stderr)
    os.execvp(command[0], command)
    return 1  # execvp does not return


if __name__ == "__main__":
    raise SystemExit(main())
