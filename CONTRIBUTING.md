# Contributing

1. Work on a branch and keep changes focused.
2. Run `python -m unittest discover -s tests -v`.
3. For frontend changes, run `node --check harness/static/app.js` and inspect the
   actual local page at both a wide and narrow browser width.
4. Describe the behavior changed, checks performed and remaining limitations.
5. Never include local `.harness` state, company sources or credentials.

The automated suite substitutes the AI responses but exercises real Git
worktrees and test subprocesses. A live Codex check is optional and consumes
account usage. Use only the generated calculator sample for that check.

GitHub Actions is provided for Windows and Linux. Other Git servers can use the
same two test commands in their own CI setup.
