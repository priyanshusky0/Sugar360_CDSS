# Prompts

Prompts are code. A small wording change can change what the LLM produces, so
they are managed like code.

## Rules

1. **Every prompt is a versioned file** in this folder, for example
   `draft_rule_v1.md`, `draft_rule_v2.md`.
2. **Never edit a prompt silently.** Once a prompt has been used for a run, do
   not change it. Create a new version instead, and note in the new file what
   changed and why.
3. **Every output records its prompt version.** Each file written to `runs/`
   must state which prompt file (and version) produced it, so any drafted rule
   can be traced back to the exact prompt behind it.

## Why

When a doctor questions a drafted rule, or results change between runs, we must
be able to answer: *which prompt produced this?* Silent edits make that
impossible and make Approach A vs. B comparisons unreliable.
