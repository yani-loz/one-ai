"""
Role: The two names the application-log capture and the RED gate's log collector must agree on —
      the logger the application logs to, and the format `app.log` records it in. A leaf of
      constants only, so neither side restates the other's literal and no gate module has to
      import a `runner_*` module to share them (fix-registry row B11).
Used by: `tools.mem01_verify.runner_logging` (the §16.16(k) capture, which keeps re-exporting
      `APP_LOGGER_NAME` for its own importers) and `tools.mem01_verify.gates.gate_red` (the
      §16.17(a) `logging`-surface collector).
Depends on: nothing — this module imports no project code, which is exactly what lets a gate
      module share it without inverting the gates ← runner layering.
Key invariants:
  - `APP_LOG_FORMAT` is the ONE format string: §16.17(a) requires the RED `logging` surface to
    see what `app.log` sees, so a change here reaches both sides at once or neither.
  - `APP_LOGGER_NAME` names the logger the runner's capture owns; its records do NOT propagate
    to the root logger while that capture is open, which is why the collector listens on both.
  - Constants only: nothing here imports, configures or touches the `logging` module, so both
    importers stay free to install their own handlers.
"""

from __future__ import annotations

#: The application logger the runner's capture owns (§16.16(k)).
APP_LOGGER_NAME: str = "app"
#: How the capture file formats a record; `logging.Formatter` appends the exception text and
#: traceback of an `exc_info` record to it, which is what the RED surface must also see.
APP_LOG_FORMAT: str = "%(asctime)s %(levelname)s %(name)s %(message)s"

__all__ = ["APP_LOGGER_NAME", "APP_LOG_FORMAT"]
