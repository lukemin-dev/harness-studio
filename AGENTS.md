# Contributor instructions

- Always verify changes and report the actual checks and remaining limitations.
- Keep runtime dependencies in Python's standard library.
- Do not silently weaken scope, review, timeout or cancellation gates.
- Do not equate simulated runs or static checks with DB/UI verification.
- Never commit `.harness`, credentials, real project sources or execution logs.
- Run `python -m unittest discover -s tests -v` for backend changes.
- Check the browser UI for frontend changes.
