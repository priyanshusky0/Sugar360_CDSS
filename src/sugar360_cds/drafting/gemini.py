"""Drafting: fill the versioned prompt with pages, ask Gemini, parse its rows.

The Gemini client is passed in rather than created here, so tests can use a
fake client and never call the API.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable

from google.genai import types

from ..evaluation.metrics import CallStats
from ..evaluation.schema import DecisionRow, Vocabulary, row_from_dict
from ..extraction.pdf_text import PageRecord

MARKER = re.compile(r"\{\{(\w+)\}\}")


def render_prompt(template: str, **values: str) -> str:
    """Replace {{name}} markers. Fails if a marker has no value or a value is unused."""
    names = set(MARKER.findall(template))
    if names != set(values):
        raise ValueError(f"prompt markers {sorted(names)} != values given {sorted(values)}")
    return MARKER.sub(lambda m: values[m.group(1)], template)


def format_pages(records: Iterable[PageRecord]) -> str:
    """Each page under a header the model must copy into its citations."""
    return "\n\n".join(f"=== doc_id: {r.doc_id} | page: {r.page} ===\n{r.full_text()}" for r in records)


def format_vocabulary(vocab: Vocabulary) -> str:
    lines = ["Inputs (condition name: allowed values):"]
    for name, spec in vocab.inputs.items():
        allowed = f"number in {spec.unit}, e.g. >=30, <45, 30-45" if spec.kind == "number" else " | ".join(sorted(spec.values))
        lines.append(f"- {name} ({spec.label}): {allowed}")
    lines += ["", "Actions (drug classes):", *(f"- {a}" for a in sorted(vocab.actions))]
    if vocab.action_types:
        lines += ["", "Action types: " + " | ".join(sorted(vocab.action_types))]
    if vocab.strengths:
        lines += ["Strengths: " + " | ".join(sorted(vocab.strengths))]
    if vocab.labels:
        lines += ["Labels: " + " | ".join(sorted(vocab.labels))]
    if vocab.skip_categories:
        lines += ["Skip categories: " + " | ".join(sorted(vocab.skip_categories))]
    return "\n".join(lines)


RETRY_CODES = {500, 503}  # server busy/hiccup: worth waiting for. 404/429 are not.
RETRY_WAITS = (30, 60, 120)  # seconds between attempts


def _generate_with_retry(client, model, prompt, config, waits=RETRY_WAITS, sleep=time.sleep):
    for wait in (*waits, None):
        try:
            return client.models.generate_content(model=model, contents=prompt, config=config)
        except Exception as e:
            if getattr(e, "code", None) not in RETRY_CODES or wait is None:
                raise
            print(f"Gemini returned {e.code}; retrying in {wait}s")
            sleep(wait)


def call_gemini(client, model: str, prompt: str, temperature: float, sleep=time.sleep) -> tuple[str, CallStats, dict]:
    """One generation call. Returns (reply text, stats, raw usage numbers).

    `seconds` includes any time spent waiting between retries.
    """
    start = time.perf_counter()
    config = types.GenerateContentConfig(temperature=temperature, response_mime_type="application/json")
    response = _generate_with_retry(client, model, prompt, config, sleep=sleep)
    seconds = time.perf_counter() - start
    u = response.usage_metadata
    candidates = getattr(response, "candidates", None) or []
    usage = {
        "prompt_tokens": u.prompt_token_count or 0,
        "output_tokens": u.candidates_token_count or 0,
        "thinking_tokens": getattr(u, "thoughts_token_count", None) or 0,  # billed as output
        # "MAX_TOKENS" = the reply was cut off at the output limit
        "finish_reason": str(getattr(candidates[0], "finish_reason", "")) if candidates else "",
    }
    stats = CallStats(usage["prompt_tokens"], usage["output_tokens"] + usage["thinking_tokens"], seconds)
    return response.text, stats, usage


def parse_reply(text: str, id_prefix: str, guideline: str = "", start: int = 1) -> tuple[list[DecisionRow], list[dict]]:
    """Turn the model's JSON into rows, numbered id_prefix+start, id_prefix+start+1, ...

    Ids are set here, not by the model, so they are unique across calls.
    Skipped items come back as dicts {text, category, doc_id, page}; prompt v2
    returned plain strings, which become {"text": ...}.
    """
    data = json.loads(text)
    rows = [
        row_from_dict({**r, "row_id": f"{id_prefix}{i}", "guideline": guideline})
        for i, r in enumerate(data.get("rows", []), start=start)
    ]
    skipped = [
        {"text": str(s.get("text", "")), "category": str(s.get("category", "")),
         "doc_id": str(s.get("doc_id", "")), "page": s.get("page", "")}
        if isinstance(s, dict) else {"text": str(s), "category": "", "doc_id": "", "page": ""}
        for s in data.get("skipped", [])
    ]
    return rows, skipped
