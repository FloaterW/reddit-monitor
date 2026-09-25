# Synthetic job-market demonstration

All employers, authors, source posts, and model responses here are fictional. This is not a collected Reddit dataset or a sample of current job-market conditions. The Reddit-shaped URLs demonstrate citation construction and should not be treated as real source links.

From the repository root:

```powershell
python demo_digest.py
```

Requires the project's Python dependencies, but no model account, Reddit access, or email credentials. The demo replaces the model boundary with deterministic fixture responses and blocks scraping, network connections, email delivery, and production history writes. It runs the real source-safe renderer, quality checks, status writer, and sanitized HTML renderer.

Files written only under ignored `data/demo-job-market/`:

- `newsletter.md`: source-linked Markdown.
- `preview.html`: visibly labeled synthetic email preview; no email is sent.
- `evaluation.json`: quality-check results, including skipped financial-domain checks.
- `status.json`: separate run outcome.
- `newsletter.work/`: local model-response/checkpoint artifacts.

`source.json` contains three fictional discussions. `model-responses.json` contains the corresponding extraction and newsletter responses. This proves the pipeline integration and profile separation, **not** real-model writing quality or live collection coverage.

The main README also gives a saved-input command for an optional actual-model preview. That path consumes provider allowance and still does not scrape or send mail.
