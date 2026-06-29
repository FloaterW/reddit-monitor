"""Tests for evaluate_digest.py — citation, dollar, numeric, and completeness checks."""

from evaluate_digest import (
    check_citation_coverage,
    check_completeness,
    check_dollar_amounts,
    check_numeric_claims,
    evaluate,
    extract_citations,
    extract_dollar_amounts,
    extract_multipliers,
    extract_percentages,
    extract_point_amounts,
)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
class TestExtractDollarAmounts:
    def test_simple(self):
        assert extract_dollar_amounts("Costs $50 total") == {"50"}

    def test_comma_separated(self):
        assert extract_dollar_amounts("Earned $1,000 bonus") == {"1000"}

    def test_decimal(self):
        assert extract_dollar_amounts("Fee is $51.25") == {"51.25"}

    def test_multiple(self):
        result = extract_dollar_amounts("Got $50 and $300 credits")
        assert result == {"50", "300"}

    def test_none(self):
        assert extract_dollar_amounts("No dollar amounts here") == set()


class TestExtractPercentages:
    def test_simple(self):
        assert extract_percentages("20% bonus") == {"20%"}

    def test_decimal(self):
        assert extract_percentages("4.5% APR") == {"4.5%"}

    def test_multiple(self):
        result = extract_percentages("20% and 30% bonuses")
        assert result == {"20%", "30%"}

    def test_none(self):
        assert extract_percentages("No percentages") == set()


class TestExtractMultipliers:
    def test_simple(self):
        assert extract_multipliers("Chase 5x on travel") == {"5x"}

    def test_case_insensitive(self):
        assert extract_multipliers("Get 5X points") == {"5x"}

    def test_none(self):
        assert extract_multipliers("No multipliers") == set()


class TestExtractPointAmounts:
    def test_k_suffix(self):
        assert extract_point_amounts("100k points signup") == {"100000"}

    def test_points_word(self):
        assert extract_point_amounts("Earned 80,000 points") == {"80000"}

    def test_miles(self):
        assert extract_point_amounts("250k miles") == {"250000"}

    def test_small_pts_ignored(self):
        assert extract_point_amounts("15 pts scored") == set()

    def test_large_points(self):
        assert extract_point_amounts("earned 80,000 points") == {"80000"}

    def test_none(self):
        assert extract_point_amounts("No point amounts") == set()


class TestExtractCitations:
    def test_single(self):
        text = "[u/alice](https://reddit.com/r/churning/comments/abc/post/123/)"
        result = extract_citations(text)
        assert len(result) == 1
        assert result[0][0] == "alice"

    def test_multiple(self):
        text = ("[u/alice](https://reddit.com/a) confirmed, "
                "[u/bob](https://reddit.com/b) agreed")
        result = extract_citations(text)
        assert len(result) == 2
        assert {r[0] for r in result} == {"alice", "bob"}

    def test_none(self):
        assert extract_citations("No citations here") == []


# ---------------------------------------------------------------------------
# Citation coverage
# ---------------------------------------------------------------------------
class TestCitationCoverage:
    def _make_comments(self, authors):
        return [{"author": a, "body": "test", "score": 1} for a in authors]

    def test_full_coverage(self):
        digest = "[u/alice](link1) and [u/bob](link2) confirmed"
        comments = self._make_comments(["alice", "bob"])
        result = check_citation_coverage(digest, comments)
        assert result["passed"]
        assert result["coverage"] == 1.0

    def test_partial_coverage(self):
        digest = "[u/alice](link1) confirmed"
        comments = self._make_comments(["alice", "bob", "charlie"])
        result = check_citation_coverage(digest, comments)
        assert result["cited_count"] == 1
        assert result["uncited_count"] == 2
        assert result["coverage"] < 0.5
        assert not result["passed"]

    def test_no_citations(self):
        digest = "A digest with no citations at all"
        comments = self._make_comments(["alice"])
        result = check_citation_coverage(digest, comments)
        assert result["cited_count"] == 0
        assert not result["passed"]

    def test_deleted_authors_excluded(self):
        digest = "[u/alice](link)"
        comments = self._make_comments(["alice", "[deleted]"])
        result = check_citation_coverage(digest, comments)
        assert result["total_authors"] == 1
        assert result["passed"]

    def test_empty_comments(self):
        result = check_citation_coverage("digest", [])
        assert result["passed"]
        assert result["total_authors"] == 0

    def test_case_insensitive(self):
        digest = "[u/Alice](link)"
        comments = self._make_comments(["alice"])
        result = check_citation_coverage(digest, comments)
        assert result["cited_count"] == 1


# ---------------------------------------------------------------------------
# Dollar amounts
# ---------------------------------------------------------------------------
class TestDollarAmounts:
    def test_all_verified(self):
        digest = "The **$50** credit and **$300** travel credit"
        comments = [{"body": "I got the $50 credit and the $300 one too"}]
        result = check_dollar_amounts(digest, comments)
        assert result["passed"]
        assert result["verified_count"] == 2

    def test_hallucinated_amount(self):
        digest = "The **$500** mega bonus is live"
        comments = [{"body": "I got the $50 credit"}]
        result = check_dollar_amounts(digest, comments)
        assert not result["passed"]
        assert "500" in result["unverified"]

    def test_no_amounts_passes(self):
        digest = "No dollar amounts mentioned"
        comments = [{"body": "Just chatting"}]
        result = check_dollar_amounts(digest, comments)
        assert result["passed"]
        assert result["total"] == 0

    def test_amount_in_any_comment(self):
        digest = "The **$75** Lululemon credit"
        comments = [
            {"body": "No amount here"},
            {"body": "The $75 credit is quarterly"},
        ]
        result = check_dollar_amounts(digest, comments)
        assert result["passed"]


# ---------------------------------------------------------------------------
# Numeric claims
# ---------------------------------------------------------------------------
class TestNumericClaims:
    def test_all_verified(self):
        digest = "20% bonus and 5x points for 100k signup"
        comments = [{"body": "There's a 20% transfer bonus and 5x on flights, 100k SUB"}]
        result = check_numeric_claims(digest, comments)
        assert result["passed"]

    def test_hallucinated_percentage(self):
        digest = "A huge 50% transfer bonus"
        comments = [{"body": "The bonus is 20%"}]
        result = check_numeric_claims(digest, comments)
        assert not result["passed"]
        assert "50%" in result["percentages"]["unverified"]

    def test_no_numerics(self):
        digest = "General discussion about cards"
        comments = [{"body": "I like credit cards"}]
        result = check_numeric_claims(digest, comments)
        assert result["passed"]


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------
class TestCompleteness:
    def test_top_comments_covered(self):
        digest = "According to u/alice and u/bob, the deal is good"
        comments = [
            {"author": "alice", "score": 50, "body": "Great deal"},
            {"author": "bob", "score": 30, "body": "Confirmed"},
            {"author": "charlie", "score": 5, "body": "Minor point"},
            {"author": "dave", "score": 2, "body": "Unimportant"},
        ]
        result = check_completeness(digest, comments)
        assert result["passed"]

    def test_missing_top_comment(self):
        digest = "According to u/charlie, things are fine"
        comments = [
            {"author": "alice", "score": 100, "body": "Big news"},
            {"author": "bob", "score": 5, "body": "Minor"},
            {"author": "charlie", "score": 3, "body": "Whatever"},
            {"author": "dave", "score": 1, "body": "Nah"},
        ]
        result = check_completeness(digest, comments)
        assert not result["passed"]
        assert any(m["author"] == "alice" for m in result["missing"])

    def test_empty_comments(self):
        result = check_completeness("digest text", [])
        assert result["passed"]
        assert result["top_count"] == 0

    def test_single_comment(self):
        digest = "u/alice reported the deal"
        comments = [{"author": "alice", "score": 10, "body": "Found a deal"}]
        result = check_completeness(digest, comments)
        assert result["passed"]

    def test_deleted_authors_skipped(self):
        digest = "Discussion happened"
        comments = [
            {"author": "[deleted]", "score": 100, "body": "High score but deleted"},
        ]
        result = check_completeness(digest, comments)
        assert result["passed"]


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
class TestEvaluate:
    def test_good_digest_passes(self):
        digest = (
            "# Daily Digest\n\n"
            "[u/alice](https://reddit.com/r/test/a) reported a **$50** credit "
            "with **20%** bonus. [u/bob](https://reddit.com/r/test/b) confirmed."
        )
        comments = [
            {"author": "alice", "score": 20, "body": "Got the $50 credit with 20% bonus"},
            {"author": "bob", "score": 15, "body": "Can confirm the $50 and 20% deal"},
        ]
        results = evaluate(digest, comments)
        assert results["citation_coverage"]["passed"]
        assert results["dollar_amounts"]["passed"]
        assert results["numeric_claims"]["passed"]
        assert results["completeness"]["passed"]

    def test_bad_digest_fails(self):
        digest = (
            "# Daily Digest\n\n"
            "A **$999** mega bonus at **99%** rate was announced."
        )
        comments = [
            {"author": "alice", "score": 20, "body": "Got the $50 credit"},
            {"author": "bob", "score": 15, "body": "Nothing special today"},
        ]
        results = evaluate(digest, comments)
        assert not results["citation_coverage"]["passed"]
        assert not results["dollar_amounts"]["passed"]
        assert not results["numeric_claims"]["passed"]
