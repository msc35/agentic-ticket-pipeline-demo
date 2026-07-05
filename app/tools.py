"""Mock investigation tools over the synthetic data.

These three functions are the agent's *only* window into the world:
  - search_logs(query, component_id=None)
  - lookup_component(component_id)
  - query_known_issues(symptom)

Every call is recorded into an **evidence trace** — the exact records each tool
returned. The grounding check in Part 5 depends on this: it verifies that the ids the
agent cites were actually surfaced by a tool, rather than invented. So the trace is not a
debugging nicety here; it is the ground truth against which the agent's claims are checked.

Matching is deliberately simple and transparent (keyword/substring overlap). No vector
search — you can read a tool call and predict exactly what it returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Data lives in ../data relative to this file, so the tools work regardless of the
# process's current working directory.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# --- data loading (cached once per process) --------------------------------------------

def _load(name: str) -> list[dict[str, Any]]:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# Loaded once at import. The synthetic data is read-only, so a module-level cache is fine.
_COMPONENTS: list[dict[str, Any]] = _load("components.json")
_LOGS: list[dict[str, Any]] = _load("logs.json")
_KNOWN_ISSUES: list[dict[str, Any]] = _load("known_issues.json")

_COMPONENTS_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in _COMPONENTS}


# --- non-recording accessors -----------------------------------------------------------
# These read component data WITHOUT touching any evidence trace. The schema (Part 3) uses
# them to validate component ids, and the safety override (Part 6) uses them to read a
# component's ASIL. Those are internal checks, not agent investigation, so they must not
# appear in the evidence trace — that is what the trace-recording Toolbox.lookup_component
# is for.

def all_component_ids() -> set[str]:
    """The set of valid component ids — the universe the schema validates against."""
    return set(_COMPONENTS_BY_ID)


def get_component(component_id: str) -> dict[str, Any] | None:
    """Return a component record (incl. safety_relevant / asil) without recording it."""
    return _COMPONENTS_BY_ID.get(component_id)


def keyword_overlap(a: str, b: str) -> set[str]:
    """Meaningful whole-word tokens shared by two texts.

    Exposed for the Part 5 grounding check, which uses it to confirm a ticket's stated root
    cause actually overlaps the known issue it cites (catching a right-citation/wrong-story
    mismatch). Same tokenizer/stopwords the tools use, so behaviour is consistent.
    """
    return _tokens(a) & _tokens(b)


# --- simple, transparent matching ------------------------------------------------------

# Stopwords: common English function/filler words that carry no technical meaning. They
# are dropped before matching so that incidental words ("can", "last", "between", "off")
# never drive a match. Note "can" is also dropped even though CAN-bus reports use it — the
# CAN examples always share several *other* technical tokens ("bus", "transmit", "error"),
# so dropping "can" removes false positives (e.g. "ECC can detect") without hurting recall.
_STOPWORDS = {
    # articles / conjunctions / prepositions
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
    "as", "from", "into", "onto", "upon", "about", "around", "across", "between", "among",
    "under", "over", "up", "down", "off", "out", "than", "then", "so", "if", "but",
    "because", "while", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "what",
    # to-be / auxiliaries / modals
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "done",
    "have", "has", "had", "can", "could", "would", "should", "will", "shall", "may",
    "might", "must",
    # pronouns / determiners / quantifiers
    "it", "its", "this", "that", "these", "those", "we", "our", "us", "you", "your",
    "they", "them", "their", "he", "she", "his", "her", "no", "not", "all", "any", "some",
    "one", "two", "three", "few", "several", "more", "most", "less", "least", "same",
    "other", "another", "each", "every", "both", "either", "neither", "such", "own",
    # generic filler verbs / adverbs / nouns
    "seem", "seems", "seemed", "look", "looks", "looking", "like", "just", "there", "here",
    "feel", "feels", "felt", "think", "thinks", "thought", "know", "knows", "known",
    "say", "says", "said", "see", "seen", "saw", "get", "got", "getting", "go", "going",
    "goes", "gone", "make", "makes", "making", "made", "want", "wants", "need", "needs",
    "way", "ways", "time", "times", "thing", "things", "something", "someone", "nothing",
    "nobody", "everyone", "everything", "anything", "anyone", "sure", "honestly", "really",
    "very", "only", "even", "still", "also", "too", "again", "once", "week", "day", "days",
    "last", "next", "prior", "new", "old", "lot", "bit", "kind", "sort", "off", "point",
    "during", "sense", "going",
}


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens, dropping stopwords and very short tokens."""
    raw = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    return {t for t in raw if len(t) > 2 and t not in _STOPWORDS}


def _match_score(query: str, *fields: str) -> int:
    """Number of meaningful *whole words* the query shares with the searchable text.

    Whole-word (token-set) overlap, not raw substring matching, is deliberate: it keeps
    behaviour predictable and avoids false positives like "intermittent" matching inside
    "intermittently". Ids such as "vdd_core" or "0x3f2a11c0" still work because they
    tokenize to whole words ("vdd", "core", "0x3f2a11c0").
    """
    return len(_tokens(query) & _tokens(" ".join(fields)))


def _matches(query: str, *fields: str, require_two: bool = False) -> bool:
    """A record matches if it shares enough meaningful keywords with the query.

    Two policies, applied per tool:

    - Logs (require_two=False): a single shared keyword is enough. Log messages are short
      and specific, so one keyword is a strong signal, and we want high recall during
      investigation. A log match on its own never creates false grounding — grounding also
      requires a matching known issue and a component connection (Part 5).

    - Known issues (require_two=True): require at least TWO shared keywords for a
      multi-word query. Known-issue descriptions are long prose, so a single incidental
      word overlap (e.g. the idiom "pin it down" brushing against "input pin") is not
      enough to call an issue relevant. This precision is what keeps the ungrounded
      examples ungrounded. A deliberately narrow one-keyword query still matches on one.
    """
    score = _match_score(query, *fields)
    if require_two and len(_tokens(query)) > 1:
        return score >= 2
    return score >= 1


class Toolbox:
    """Holds the three tools plus the evidence trace for a single pipeline run.

    Create a fresh Toolbox per report so each run has its own isolated trace. The pipeline
    (Part 7) reads `evidence_trace` and `surfaced_ids()` afterward to drive the grounding
    check (Part 5).
    """

    def __init__(self) -> None:
        # One entry per tool call: {"tool": str, "input": dict, "records": list[dict]}.
        self.evidence_trace: list[dict[str, Any]] = []

    # -- internal ---------------------------------------------------------------------

    def _record(self, tool: str, tool_input: dict[str, Any], records: list[dict[str, Any]]) -> None:
        """Append one tool call and its raw returned records to the evidence trace."""
        self.evidence_trace.append(
            {"tool": tool, "input": tool_input, "records": records}
        )

    # -- the three agent-facing tools -------------------------------------------------

    def search_logs(self, query: str, component_id: str | None = None) -> list[dict[str, Any]]:
        """Return log entries matching `query`, optionally filtered to one component.

        A log matches if a query token appears in its message (or its component id). The
        optional `component_id` narrows results to a single component.
        """
        results = []
        for log in _LOGS:
            if component_id and log["component_id"] != component_id:
                continue
            # Match on the message text only; component filtering is the param's job, so
            # keeping the id out of the searchable text avoids "cmp" matching everything.
            # Logs use the permissive single-keyword policy for investigation recall.
            if _matches(query, log["message"]):
                results.append(log)
        self._record("search_logs", {"query": query, "component_id": component_id}, results)
        return results

    def lookup_component(self, component_id: str) -> dict[str, Any] | None:
        """Return the component record (including safety_relevant / asil), or None.

        The safety fields returned here are what the Part 6 safety override keys on.
        """
        component = _COMPONENTS_BY_ID.get(component_id)
        # Record as a list for a uniform trace shape ([] when the id is unknown).
        self._record(
            "lookup_component",
            {"component_id": component_id},
            [component] if component else [],
        )
        return component

    def query_known_issues(self, symptom: str) -> list[dict[str, Any]]:
        """Return known-issue entries whose pattern/root_cause matches `symptom`.

        May return an empty list — that is a valid and important outcome, because it is
        how a report ends up ungrounded (no supporting known issue exists).
        """
        results = []
        for issue in _KNOWN_ISSUES:
            # Match on the human-readable symptom text (pattern + root cause), not the
            # component id, since this tool is queried by symptom, not by component.
            # Known issues use the precise two-keyword policy — this is the match that
            # drives grounding, so it must not fire on incidental single-word overlap.
            if _matches(symptom, issue["pattern"], issue["root_cause"], require_two=True):
                results.append(issue)
        self._record("query_known_issues", {"symptom": symptom}, results)
        return results

    # -- helpers for the grounding check ----------------------------------------------

    def surfaced_ids(self) -> set[str]:
        """The set of every record id the tools actually returned this run.

        Part 5 checks that each id the agent cites in `evidence_ids` is in this set;
        anything cited but not surfaced here is hallucinated grounding.
        """
        ids: set[str] = set()
        for call in self.evidence_trace:
            for record in call["records"]:
                if "id" in record:
                    ids.add(record["id"])
        return ids

    def surfaced_records(self) -> dict[str, dict[str, Any]]:
        """Map each surfaced record id -> the record itself.

        Part 5's grounding check uses this to resolve a cited id back to its record and
        confirm the evidence actually links to the claimed component.
        """
        records: dict[str, dict[str, Any]] = {}
        for call in self.evidence_trace:
            for record in call["records"]:
                if "id" in record:
                    records[record["id"]] = record
        return records


if __name__ == "__main__":
    # Small self-demonstration: run one call of each tool and show what it surfaced.
    tb = Toolbox()

    print("search_logs('brownout undervoltage', 'CMP-002'):")
    for r in tb.search_logs("brownout undervoltage", "CMP-002"):
        print(f"  {r['id']} [{r['severity']}] {r['message']}")

    print("\nlookup_component('CMP-002'):")
    print(f"  {tb.lookup_component('CMP-002')}")

    print("\nquery_known_issues('PMU brownout on VDD_CORE'):")
    for r in tb.query_known_issues("PMU brownout on VDD_CORE"):
        print(f"  {r['id']} -> {r['root_cause']}")

    print("\nquery_known_issues('intermittent testbench timeout flakiness'):")
    print(f"  matches: {tb.query_known_issues('intermittent testbench timeout flakiness')}")

    print(f"\nsurfaced_ids across the run: {sorted(tb.surfaced_ids())}")
    print(f"evidence_trace has {len(tb.evidence_trace)} tool calls recorded")
