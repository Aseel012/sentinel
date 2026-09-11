# Contributing

Keep layer boundaries intact: models do no I/O, collectors do not diagnose, application services do not format output, and CLI code does not inspect Linux directly. Use the standard library unless a dependency has a compelling operational justification.

Run `python -m unittest discover -s tests -v`, `python -m compileall -q sentinel`, and `git diff --check` before proposing a change. Tests must be non-destructive and must not require root.
