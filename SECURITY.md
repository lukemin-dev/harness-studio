# Local execution boundary

Harness Studio is a single-user localhost tool. Do not expose its port through a
proxy or listen on a shared network interface. Host/Origin checks and a per-process
CSRF token reduce browser-based cross-origin access; they do not protect against
other programs already running as your local user.

Registered test commands execute with your own privileges. Worktrees share Git
metadata with the source repository and are not security sandboxes. Review code
and commands before running unfamiliar projects.

Codex is invoked with workspace-write for implementation, read-only for review,
and approval mode never. No bypass-sandbox flag is passed. Actual platform
capabilities depend on the installed Codex version and configuration.

Credentials should remain in the CLI's own authentication storage. Do not put
credentials into task prompts or project rules. Logs may contain source content;
local `.harness` data is excluded from Git but is not encrypted.

For a suspected issue, do not post secrets in a public issue. Provide a minimal
reproduction using a dummy local repository.
