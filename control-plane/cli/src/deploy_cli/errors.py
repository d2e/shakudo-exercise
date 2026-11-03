"""Exit codes (CL2) and the one exception the commands raise."""
from typing import Optional

EXIT_OK = 0
EXIT_FAIL = 1   # the API or the network failed the request
EXIT_USAGE = 2  # bad flags, or an action the CLI refused to take
EXIT_AUTH = 3   # authentication needed or failed


class CliError(Exception):
    """A readable failure. `message` and `hint` are shown, never a traceback (CL4)."""

    def __init__(self, message: str, code: int = EXIT_FAIL, hint: Optional[str] = None):
        super().__init__(message)
        self.message, self.code, self.hint = message, code, hint
