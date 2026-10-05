<!-- prompt: draft_table | version: v3 | created: 2026-10-05 | do not edit: make v4 instead -->
<!-- changes from v2 (docs/decisions.md D13): never drop a condition (other_conditions);
     one continuous quote of >= 8 words or a full cell/list item with its heading; strength
     "preferred"; row label stated/inferred; recommendation as written; draft instead of skip -->
You are helping clinicians build a decision table from ONE diabetes guideline:
{{guideline}}.
Your draft will be checked by doctors, and every quote and heading will be
checked by a program against the page you cite.

## Question the table must answer

{{question}}

## Rules

1. Use ONLY the pages below. Do not use outside knowledge.
2. Extract EVERY recommendation on these pages about which glucose-lowering
   medicine to start, add, switch, continue, reduce, stop or avoid - including
   dose changes or stopping because of kidney function.
3. Each row is one recommendation: IF all its conditions hold THEN the action.
4. NEVER DROP A CONDITION. Every condition in the quote must appear in the row:
   - in `conditions`, using only vocabulary inputs and values. Numbers exactly
     as the source gives them, as ">=30", ">7", "<45" or "30-45", in the
     vocabulary unit. Heart failure: use "stable" or
     "unstable_or_hospitalized" when the source says so, "yes" otherwise.
   - otherwise in `other_conditions`, in the source's own words (for example
     "moderate to end-stage renal disease", "if SGLT2i and metformin are
     insufficient", "history of bladder cancer").
   Leave out inputs the source does not mention. If the source says the rule
   applies to patients with A OR B, write one row for A and one row for B.
5. `recommendation`: the action as the source writes it, including
   combinations (for example "Basal insulin + metformin + another OAD, OR
   triple OAD combination"). One recommendation = one row: if one sentence
   offers alternatives, keep them in one row.
6. `actions`: every drug class from the vocabulary named in the recommendation.
7. `action_type`: one of the vocabulary action types.
8. `strength`: "preferred" for preferred / first choice / first-line;
   "recommended" for recommend / should; "consider" for may / consider / can /
   suggest.
9. `label`: "stated" if every condition and the action are written in the
   quote; "inferred" if you mapped, assumed or added anything (for example
   "on metformin" when the quote only lists a regimen that contains metformin).
   Give the reason in `label_reason`.
10. `checks`: what must be verified before acting. `cautions`: risks or side
    effects to warn about. Each has `basis`: "stated" if the source says it, or
    "inferred" if it follows directly from the quoted text. Never add checks or
    cautions from general knowledge.
11. `sources`: at least one. `doc_id` and `page` exactly as in the page header.
    `quote`: ONE continuous passage copied word for word from ONE page - never
    joined from several places, never shortened with "..." - containing the
    recommendation and its conditions. At least 8 words; if the recommendation
    is a table cell or list item shorter than that, quote the whole cell or
    item and put the table title or list heading it belongs to in `heading`
    (also copied word for word from the same page). Otherwise `heading` is "".
12. Table cells that describe drug properties (efficacy, weight effect, cost,
    side effects) are not recommendations: do not turn them into rows.
13. Do not skip a recommendation because it does not fit the inputs: draft it
    with `other_conditions` and label it "inferred". Put an item in `skipped`
    only if it is not a drug recommendation, with a `category`:
    "not_a_drug_recommendation" (monitoring, general advice, drug properties),
    "other_topic" (a drug decision outside this table, e.g. blood pressure), or
    "cannot_express" (a drug recommendation you could not write as a row at all).

## Vocabulary

{{vocabulary}}

## Output

Return JSON only, in exactly this shape:

{"rows": [{"recommendation": "<as written>", "conditions": {"<input>": "<value>"}, "other_conditions": ["<source words>"], "actions": ["<drug class>"], "action_type": "<action type>", "strength": "<strength>", "label": "stated", "label_reason": "", "checks": [{"text": "<check>", "basis": "stated"}], "cautions": [{"text": "<caution>", "basis": "stated"}], "sources": [{"doc_id": "<doc_id>", "page": <page number>, "heading": "", "quote": "<exact words>"}]}], "skipped": [{"text": "<what the source says>", "category": "<category>", "doc_id": "<doc_id>", "page": <page number>}]}

## Pages from {{guideline}}

{{pages}}
