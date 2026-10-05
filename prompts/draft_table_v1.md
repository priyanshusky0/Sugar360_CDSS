<!-- prompt: draft_table | version: v1 | created: 2026-10-05 | do not edit: make v2 instead -->
You are helping clinicians build a decision table from diabetes guidelines.
Your draft will be checked by doctors, and every quote will be checked by a
program against the page you cite.

## Question the table must answer

{{question}}

## Rules

1. Use ONLY the guideline pages given below. Do not use outside knowledge.
2. Each row is one recommendation: IF all its conditions hold THEN one action.
3. `conditions`: use only factors and values from the vocabulary. Include only
   the factors the source says matter for this recommendation. Do not add
   factors the source does not mention.
4. `action`: exactly one drug class from the vocabulary. If a recommendation
   cannot be expressed with the vocabulary, leave it out and describe it in
   `skipped`.
5. `sources`: at least one. `doc_id` and `page` exactly as shown in the page
   header. `quote` copied word for word from that page (at most 40 words). Do
   not paraphrase, shorten inside the quote, or fix spelling.
6. `checks`: safety checks the source states before acting (for example
   contraindications, kidney function limits, hypoglycaemia risk). Use an empty
   list if the source states none.
7. If guidelines disagree, write separate rows, each with its own source. Do
   not merge them.
8. Do not invent. Fewer well-supported rows are better than many weak ones.

## Vocabulary

{{vocabulary}}

## Output

Return JSON only, in exactly this shape:

{"rows": [{"row_id": "1", "conditions": {"<factor>": "<value>"}, "action": "<drug class>", "sources": [{"doc_id": "<doc_id>", "page": <page number>, "quote": "<exact words>"}], "checks": ["<check>"]}], "skipped": ["<recommendation that did not fit the vocabulary, with doc_id and page>"]}

## Guideline pages

{{pages}}
