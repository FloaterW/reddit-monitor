# Monitor and editorial profiles

Run commands from the repository root. Add a JSON file to `config/monitors/`, then select its filename (without `.json`) with `--monitor`.

```json
{
  "name": "software-release-watch",
  "description": "Discussion of software releases and migration experiences.",
  "subreddits": ["programming"],
  "keywords": ["release", "migration", "breaking change"],
  "post_sort": "new",
  "time_filter": "day",
  "posts_per_subreddit": 10,
  "title_filters": {},
  "digest": {
    "title": "Software Release Digest",
    "audience": "developers maintaining production applications",
    "editorial": {
      "preset": "general",
      "priorities": "New releases, migration requirements, compatibility reports, and unresolved regressions. Distinguish user reports from confirmed release notes.",
      "style": "Use descriptive topic headings and concise bullets. Preserve version and platform context where stated.",
      "financial_checks": false
    }
  }
}
```

This is an illustrative configuration, not a claim that these communities or keywords were tested live.

## Settings

| Setting | Behavior |
| --- | --- |
| `name`, `subreddits`, `keywords` | Required nonempty profile name and lists of nonempty strings |
| `description` | Human-readable monitor purpose; also used by the legacy Markdown path |
| `post_sort`, `time_filter`, `posts_per_subreddit` | Collection settings; CLI overrides take precedence |
| `title_filters` | Optional per-subreddit title regexes |
| `digest.title` | Email title; also the source-safe general profile's document heading |
| `digest.audience` | Readers for whom the model should select and explain information |
| `digest.editorial.preset` | `general` (default) or `churning` |
| `digest.editorial.priorities` | Optional nonempty relevance instructions, at most 8,000 characters |
| `digest.editorial.style` | Optional nonempty presentation instructions, at most 8,000 characters |
| `digest.editorial.financial_checks` | Boolean; defaults to false for general and true for churning |

The editable settings are trusted operator configuration, not fields to populate from scraped comments. Unknown editorial settings, unknown presets, malformed text, and string-valued booleans are rejected. Keep actual credentials out of profiles.

Both presets support configurable priorities and style. Churning supplies the established newsletter instructions; general supplies topic-neutral instructions. Defaults are resolved per run, without global mutations between profiles. Audience and priorities reach both extraction and writing in source-safe mode. Style instructions apply to writing. In legacy Markdown mode, a general profile's instructions also reach the combining step.

## Shared versus optional checks

Source-safe mode requires valid source IDs, safely constructed Reddit citations, supported numeric claims, sane structure, and editorial checks during generation. An invalid draft is repaired within bounded limits or rejected. Turning off the financial option does **not** turn off those shared safeguards.

The financial option enables narrow checks for transfer bonuses, combined credits, net costs, and percentage-based spending requirements. These are domain-specific heuristics, not a general arithmetic or financial fact checker. Their disabled state appears as `SKIP` in evaluation reports.

Use `--source-safe --quality strict` for the fully gated workflow. The legacy free-form Markdown path remains available, but does not offer the same generation-time structure guarantees. Evaluation defaults to `warn` unless you explicitly select strict mode or use the supplied scheduled wrapper.

Standalone evaluation can use the same profile:

```powershell
python evaluate_digest.py data/demo-job-market/newsletter.md examples/job-market/source.json --monitor job-market
```

Without `--monitor`, standalone evaluation retains its historical financial-check default. It also treats raw author coverage as required; the source-safe pipeline labels raw author coverage and high-score coverage as diagnostics, not guarantees of completeness.

## Compatibility

- The `churning` monitor explicitly selects its preset and financial checks. The refactor preserves its previous prompts byte-for-byte, its collection settings, and its existing Windows wrappers.
- `job-market` now has its own audience, priorities, and style for both summarization paths.
- Other monitor profiles that omit `digest.editorial` use the general preset.
- No-profile/no-topic-override CLI calls keep historical churning defaults. Explicit `--subreddits` or `--keywords` without a monitor selects general editorial behavior; specify both when you want to replace both legacy collection defaults.
- There is no automatic scheduling, additional live collection, or provider change when adding a profile.

## Test safely

Start with `python demo_digest.py` for a fully offline fixture-based demonstration. For actual model testing, use saved input with `--from-json`, `--no-email`, `--no-db`, and separate `--save` and `--status-file` paths under `data/`.

Saved-input replay uses the supplied records; selecting a different profile does not re-filter or re-scrape that file. `--digest-date` labels the edition and does not recover missing history. A real model invocation consumes the provider's usage allowance.
