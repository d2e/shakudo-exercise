"""Everything the commands need from the outside world, injected so tests need no terminal, clock or network."""
import getpass
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Mapping, TextIO


@dataclass
class Context:
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    stdin_tty: bool
    stdout_tty: bool
    now: Callable[[], float]
    sleep: Callable[[float], None]
    prompt_secret: Callable[[str], str]
    getuser: Callable[[], str]

    @classmethod
    def system(cls) -> "Context":
        return cls(env=os.environ, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                   stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), now=time.time,
                   sleep=time.sleep, prompt_secret=getpass.getpass, getuser=getpass.getuser)

    @property
    def color(self) -> bool:
        """CL3: colour only on a terminal, never when NO_COLOR is set."""
        return self.stdout_tty and "NO_COLOR" not in self.env

    def out(self, text: str = "") -> None:
        self.stdout.write(text + "\n")

    def err(self, text: str = "") -> None:
        self.stderr.write(text + "\n")
