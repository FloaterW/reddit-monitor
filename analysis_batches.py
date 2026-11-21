"""Size enhanced extraction batches using the actual, shared-context JSON."""

import json


def chunk_sources(sources, *, max_chars=80000, max_sources=80):
    from verified_digest import _source_records

    chunks, current = [], []
    for source in sources:
        candidate = current + [source]
        if current and (len(candidate) > max_sources or len(json.dumps(_source_records(candidate), ensure_ascii=False)) > max_chars):
            chunks.append(current)
            current = []
        # A single oversize source remains intact. The existing overall model
        # timeout applies; this function never drops source text to fit a budget.
        current.append(source)
    if current:
        chunks.append(current)
    return chunks
