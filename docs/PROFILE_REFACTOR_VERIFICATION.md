# General-purpose profile refactor verification

## Scope

Separate monitor-specific editorial instructions from the shared collection, summarization, validation, and email pipeline. Present the churning newsletter as a real-world case study and add a synthetic job-market demonstration. Preserve the established scheduled workflow.

## Compatibility checks

- The four churning prompt literals are SHA-256 checked against release `9abb365` in the offline regression suite.
- A deterministic two-stage replay produced identical extraction and writing requests before and after the refactor.
- The saved September 24 churning document rendered identically against its original saved sources, with no validation errors. This was an offline comparison, not a new generation or email.
- Churning collection configuration is unchanged apart from explicit editorial preset/check selection. The scheduler wrappers are unchanged.
- Financial-domain checks remain enabled for churning. Disabling them for another profile does not disable citation integrity, source grounding, structural checks, or editorial checks.

## Second use case

The job-market monitor passes its audience, priorities, and style into its own generation requests. Tests cover both source-safe and legacy Markdown paths, including multi-batch synthesis. Profile configuration cannot mutate another profile, and profile-dependent prompts receive separate checkpoint hashes.

`python demo_digest.py` uses fictional comments and fixed model responses to exercise the real pipeline, source checks, status output, and sanitized HTML preview. It blocks external connections, live scraping, delivery, and production history writes. Production run status remains unchanged. The HTML preview is explicitly labeled synthetic.

This demonstrates integration and configurability; it is not a live-model quality benchmark or proof of job-market collection coverage.

## Release gates

Run the full offline suite, Ruff, compilation, dependency audit, exact-private-value checks, and redacted Gitleaks scans before publication. GitHub's Windows/Linux Python 3.10/3.12 matrix and CodeQL must pass for the exact PR head before merge. Follow the PR checks for the final CI result.

The repository's pre-existing historical personal-email metadata remains outside this refactor; no history rewrite is performed. No credentials, raw production editions, model artifacts, local notes, or private audit scripts belong in this release.
