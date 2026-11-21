"""Bounded published-story fingerprints with retrieval of complete relevant stories.

Fingerprints are discovery hints, not evidence or a substitute for qualifications
in the original published story. This module never reads or writes a database.
"""

import json
import re

_STOP = set("a an the to for from of on in at by with and or but is are was were be been this that these those it its i my we our you your they their as has have had not no reported reports report says said notes note user users card cards credit business offer offers points point bonus spend spending new update updates discussion question thread daily weekly".split())


def _tokens(value):
    return {token for token in re.findall(r"[a-z][a-z0-9]{2,}", value.casefold()) if token not in _STOP}


def populate_history(editions, grouped, current_sources, *, edition_limit, total_limit):
    """Populate existing edition envelopes without changing whole-story semantics."""
    from editorial_selection import _cards, amounts

    queries, current_ids = [], set()
    for row in current_sources:
        if row.get("context_only"):
            continue
        body = str(row.get("body") or "")
        queries.append((_tokens(body), amounts(body), set(_cards(body))))
        current_ids.add(row.get("id"))
        current_ids.update(p.get("id") for p in row.get("parent_context", []) if isinstance(p, dict))
    current_ids.discard(None)

    def score(group):
        topic, numbers, cards = _tokens(group["topic"]), amounts(group["text"]), set(_cards(group["text"]))
        exact = 100 if current_ids & set(group["source_ids"]) else 0
        best = 0
        for tokens, values, entities in queries:
            shared = len(topic & tokens)
            entity = bool(cards & entities)
            if shared >= 2 or entity:
                best = max(best, min(shared, 8) * 3 + 12 * entity + min(len(numbers & values), 6) * 2)
        return exact + best

    ranked = [sorted(range(len(groups)), key=lambda i: (-score(groups[i]), i)) for groups in grouped]
    index_limit = min(total_limit - 8000, int(total_limit * .5))
    for edition, groups in zip(editions, grouped):
        edition["story_index_columns"] = ["story", "topic_hint", "numeric_tokens"]
        edition["story_index"] = []
        edition["coverage"].update(
            included_story_count=0, omitted_story_count=len(groups),
            index_story_count=0, index_omitted_story_count=len(groups), index_complete=not groups,
            index_fields_partial=True, retrieval="current-source relevance with one full-story opportunity per edition",
            note="Previously delivered text only. Compact index fields are partial discovery hints, not evidence: numbers may be dates or unrelated terms. Only items contain complete stories and qualifications. Missing index/full-story coverage is not proof of novelty.")
        edition["coverage"]["omitted_topics"] = sorted(
            (g["topic"][:56] for g in groups), key=lambda t: len(json.dumps(t, ensure_ascii=False)), reverse=True)[:6]

    def size():
        return len(json.dumps(editions, ensure_ascii=False))

    # Round-robin gives older editions index space instead of letting newer
    # newsletters consume the complete discovery budget first.
    for position in range(max(map(len, ranked), default=0)):
        for edition, groups, order in zip(editions, grouped, ranked):
            if position >= len(order):
                continue
            i = order[position]
            group = groups[i]
            # Complete source IDs/authors stay with retrieved whole stories;
            # repeating them in every hint would crowd out qualified history.
            compact = [i, group["topic"][:48], sorted(amounts(group["text"]))[:4]]
            edition["story_index"].append(compact)
            if size() > index_limit:
                edition["story_index"].pop()
    for edition, groups in zip(editions, grouped):
        edition["story_index"].sort(key=lambda item: item[0])
        edition["coverage"].update(index_story_count=len(edition["story_index"]),
                                   index_omitted_story_count=len(groups)-len(edition["story_index"]),
                                   index_complete=len(groups) == len(edition["story_index"]))

    used = [0] * len(editions)
    included = [set() for _ in editions]

    def admit(e, i):
        edition, group = editions[e], grouped[e][i]
        if i in included[e] or used[e] + len(group["text"]) > edition_limit:
            return
        edition["items"].append(group["text"])
        edition["provenance"].append({"item_index": len(edition["items"])-1, "story_index": i,
                                      "source_ids": group["source_ids"], "authors": group["authors"]})
        edition["coverage"]["included_story_count"] += 1
        edition["coverage"]["omitted_story_count"] -= 1
        if size() > total_limit:
            edition["items"].pop()
            edition["provenance"].pop()
            edition["coverage"]["included_story_count"] -= 1
            edition["coverage"]["omitted_story_count"] += 1
            return
        used[e] += len(group["text"])
        included[e].add(i)

    # Give each older edition's best-fitting story a chance, then spend the
    # remaining text budget on globally relevant complete stories.
    for e, order in enumerate(ranked):
        for i in order:
            if len(grouped[e][i]["text"]) <= edition_limit:
                admit(e, i)
                break
    candidates = sorted(((score(group), e, i) for e, groups in enumerate(grouped) for i, group in enumerate(groups)),
                        key=lambda item: (-item[0], item[1], item[2]))
    for _, e, i in candidates:
        admit(e, i)
    for e, (edition, groups) in enumerate(zip(editions, grouped)):
        omitted = [g["topic"][:56] for i, g in enumerate(groups) if i not in included[e]]
        edition["coverage"].update(omitted_topics=omitted[:6], published_stories_complete=not omitted)
    if size() > total_limit:
        raise ValueError("Published-history metadata exceeds its configured budget")
    return editions
