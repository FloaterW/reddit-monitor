from editorial_trace import build_source_trace


def test_trace_preserves_actual_stage_reasons_without_inventing_extraction_judgments():
    sources = [{"id": str(i), "subreddit": "test"} for i in range(4)]
    notes = [{"text": "detail", "source_ids": [str(i)]} for i in range(3)]
    payload = {"notes": [{**n, "note_id": f"n{i}"} for i, n in enumerate(notes)]}
    plan = {"stories": [{"topic": "News", "status": "new", "note_ids": ["n0", "n1"], "reason": "Complementary reports"},
                        {"topic": "Repeat", "status": "repeat", "note_ids": ["n2"], "reason": "Same author and outcome"}]}
    doc = {"sections": [{"title": "News", "bullets": [{"source_ids": ["0"]}]}]}
    trace = build_source_trace(sources, notes, doc, plan=plan, payload=payload)
    assert [r["outcome"] for r in trace["sources"]] == ["published", "selected_not_cited", "planner_excluded", "not_extracted"]
    assert trace["sources"][3]["omission_review"] is None
    assert trace["sources"][2]["planner_decisions"][0]["reason"] == "Same author and outcome"
    assert "not retention of every fact" in trace["meaning"]


def test_trace_distinguishes_model_review_decision_from_measured_provenance():
    review = {"candidate_count": 2, "reviewed_count": 1, "unreviewed_count": 1,
              "decisions": [{"source_id": "a", "status": "insufficient_context", "reason": "No known referent"}]}
    trace = build_source_trace([{"id": "a"}], [], {}, review=review)
    assert trace["sources"][0]["omission_review"]["reason"] == "No known referent"
    assert trace["review"]["unreviewed_count"] == 1
    assert trace["counts"] == {"not_extracted": 1}
