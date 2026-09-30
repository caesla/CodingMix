# Working in this repository

- Public repository. Never commit personal data: no emails, no local paths, no tokens, no real hook payloads.
- Git identity must be `caesla <219047344+caesla@users.noreply.github.com>`. Check with `git config user.email` before the first commit of a session.
- Code, comments, CLI output and README in English. The design spec in docs/superpowers/specs is Italian.
- Never use the em dash or en dash as punctuation.
- Run `uv run pytest` and `uv run ruff check` before every commit.
- Tests must not touch the network, the OS keyring, real user directories or real Claude Code settings.
