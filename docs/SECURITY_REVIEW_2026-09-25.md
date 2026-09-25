# Release security review — September 25, 2026

Status: scoped audit completed; final staged-tree secret scan passed.

## Scope and method

Reviewed the latest local application, provider adapters, scheduler wrappers, quality gates, tests, ignore rules, CI configuration, and repository history before publication. No production digest, model generation, or email delivery was triggered. Windows task timing, provider selection, keyword configuration, and credentials were not changed by the security fixes.

Checks included:

- Exact-value comparison of publishable files and 126 reachable historical blobs against locally configured private email/credential values, without printing those values.
- Gitleaks 8.30.1 scanning all reachable local Git history: 23 commits, no detected leaks. The official Windows scanner archive was verified against the release's SHA-256 digest before execution.
- Gitleaks scanning an export of the exact staged release tree: no detected leaks. Local credentials, run artifacts, and planning notes are excluded from that tree.
- GitHub secret-scanning and code-scanning alert queries: no existing alerts returned. This is not a claim that the newly published revision has already passed GitHub CI.
- Locked runtime/development dependency audit: no known vulnerabilities reported by `pip-audit` at review time; `pip check` passed.
- Manual review of credential loading, LLM subprocess arguments and environment, untrusted-input handling, email sanitization, XML parsing, SQL use, and workflow permissions.

## Findings and dispositions

| Severity | Finding | Disposition |
| --- | --- | --- |
| High | SMTP connections used the standard library's implicit context without explicitly requiring certificate/hostname validation. | Fixed in digest delivery, failure alerts, and the scheduled diagnostic using `ssl.create_default_context()`. Regression tests verify every call site and block authentication after certificate rejection. |
| Medium — privacy | A personal email address exists in author/committer metadata in already-published history. One unpublished predecessor also used it. | New release commits use the account's GitHub no-reply identity. The unpublished predecessor is excluded from the release ancestry while its code changes are preserved. Existing public history is not rewritten by this release; coordinated cleanup remains a separate decision. |
| Medium — prevention | Ignore rules did not cover local planning notes, environment backups, common private-key files, and databases outside `data/`. | Added exclusions and offline regression tests. Local files remain on disk, not in the release. |
| Low | Two new test modules had import-order lint failures; README provider and page-limit text was stale. | Corrected imports and documentation. |
| Medium — compatibility | Fresh GitHub CI found that Python 3.10 rejected RSS timestamps ending in `Z`, preventing the old-page cutoff. Nonzero offsets also needed conversion before applying a UTC label. | Normalize the UTC suffix and timezone before formatting; add direct regression cases. |

No active SMTP credential, configured credential-file path, or configured private email value was found in the scanned source-file contents. The email finding is in Git metadata. Pattern-based scans can miss unknown secret formats; no credential rotation was performed because these checks did not identify a leaked active secret.

## Verification

- 375 offline tests passed on Windows / Python 3.12 after the CI-discovered timestamp correction.
- Coverage: 78.45% across the CI-selected modules, above the 60% gate.
- Ruff, compilation, and dependency consistency checks passed.
- A live SMTP connection completed a certificate-verified TLS 1.3 handshake and returned `250` to `NOOP`. No login and no email send were performed.
- New security tests exercise all three SMTP call sites, certificate rejection, private-file exclusions, and the publishable `.env.example` exception.

## Remaining limitations and follow-ups

- Previously published personal commit metadata remains visible. A normal new commit or `.gitignore` change cannot remove it from existing history, clones, caches, or PR references. A destructive rewrite/force-push was not performed.
- Reddit collection is bounded and can be throttled or incomplete. Current pacing is not a verified safe rate and does not yet interpret all rate-limit headers. This review does not expand scraping or certify access permission.
- Source material and model artifacts can contain third-party personal information. Local retention/deletion and host access controls require ongoing attention; they are not solved by excluding files from Git.
- Provider CLI flags and sandbox behavior depend on installed versions. Tests cover command construction and output rejection, not a formal isolation proof.
- No penetration test, host-wide malware scan, independent cryptographic audit, or exhaustive factual review of generated newsletters was performed.

Reference: Python's [SSL security guidance](https://docs.python.org/3.12/library/ssl.html#security-considerations) explains certificate and hostname verification with a default client context.
