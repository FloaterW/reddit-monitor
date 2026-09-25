# Security and privacy

## Operating safely

- Keep Gmail app passwords outside the repository and synced folders. Use the documented external credential file or a process environment variable. Restrict the file to the account running the task.
- Never commit real `.env` files, authentication state, generated newsletters, raw Reddit data, run databases, logs, or local investigation notes. `.gitignore` is a safeguard, not a secret scanner; `git add -f` can bypass it.
- SMTP connections explicitly verify the server certificate and hostname using `ssl.create_default_context()`. Certificate failures stop delivery; do not disable verification to work around them.
- Treat Reddit content and model output as untrusted. The supported provider adapters disable tools and integrations, use temporary working directories, and exclude SMTP credentials from the child environment. Model prompts alone are not a security boundary.
- Generated email strips active HTML, images, and non-Reddit links. Source-safe strict mode also blocks delivery when required validation fails. These checks are not a general fact checker or a guarantee against every prompt-injection technique.
- Saved source data and model drafts can contain third-party personal information. Keep them local, restrict access, and apply appropriate retention/deletion policies. Do not upload them as public bug-report attachments.
- Provider authentication remains accessible to the CLI running under your account. Keep the machine, account, dependencies, and CLI installation trusted. This is a local single-user application, not a hardened multi-tenant service.
- Follow Reddit's current access and data-use requirements. Available RSS/HTML endpoints and request pacing do not establish permission or guarantee complete coverage.

## Before publishing

1. Review the exact staged files, including new files and commit author/committer metadata.
2. Scan both the proposed tree and reachable Git history with a secret scanner, with redacted output.
3. Audit locked dependencies and run the offline test suite and lint checks.
4. Use a GitHub no-reply commit identity if personal email privacy is required.
5. If a credential was ever published, revoke/rotate it. Deleting the current file does not remove earlier commits, clones, caches, or pull-request refs. Coordinate any history rewrite rather than force-pushing unexpectedly.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting option if it is enabled for this repository. Otherwise, open an issue requesting a private reporting channel without including credentials, private data, or exploit details that expose an account.

## Audit scope

The September 2026 release review covers repository/publication privacy, reachable local Git history, credential loading, provider subprocess isolation, generated email sanitization, SQLite parameterization, SMTP TLS, CI permissions, and locked dependency advisories. Findings and verification are documented in [the release review](docs/SECURITY_REVIEW_2026-09-25.md).

An audit is a point-in-time assessment, not a guarantee of zero vulnerabilities. It does not certify Reddit access rights, validate every generated financial claim, or inspect every file on the host machine.
