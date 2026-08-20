---
name: show-me-sequence
description: Convert any business workflow, system interaction, API call chain, event flow, fulfillment process, approval process, or incident timeline into a crisp, minimal, phased UML-style sequence diagram where every step has a clear directional arrow, the business action is readable in the user's language, and the optional core method name appears beneath it. Use when Codex needs to create, redraw, standardize, validate, or update a sequence diagram and deliver editable SVG or high-resolution PNG, especially from prose, requirements, tickets, tables, screenshots, Mermaid, PlantUML, or an existing diagram.
---

# Show Me Sequence

Create a business-readable sequence diagram with deterministic layout. Keep semantics in JSON and render with the bundled script; never hand-draw the final SVG.

## Workflow

1. Extract participants, causal events, phases, conditions, states, notes, and any genuinely useful execution spans. Do not invent missing behavior.
2. Order participants from initiator to downstream dependencies. Prefer 4–9 participants; merge lifelines that add no business meaning.
3. Group the flow into 2–6 outcome-oriented phases.
4. Write every business-visible label in the user's language. For a Chinese request or title, write Chinese action phrases such as `创建远程沙箱`; never use a bare method or term such as `Sandbox.create` as the main label.
5. Put only the exact core method name in optional `method`, for example `{"label":"创建远程沙箱","method":"Sandbox.create(scope)"}`. Omit `method` when the step has no real implementation method. Do not put field names, event names, protocols, parameters lists, return values, or explanatory sentences there.
6. Model nonlinear behavior explicitly:
   - Use `alt` for mutually exclusive outcomes.
   - Use `opt` for optional behavior.
   - Use `loop` for retry or repetition.
   - Use `par` for concurrent branches.
   - Use `break` for an early termination or exceptional exit.
7. Omit activations by default. Add one only when a short synchronous execution span materially helps the reader; never use an activation to show object lifetime, resource existence, ownership, or an entire request lifecycle.
8. Create a JSON specification. Read [references/specification.md](references/specification.md) for the complete model. Start from [assets/example-sequence.json](assets/example-sequence.json) when useful.
9. Validate before rendering:

   ```bash
   python3 scripts/render_sequence.py input.json --check
   ```

10. Render editable SVG, selecting a target preset when appropriate:

   ```bash
   python3 scripts/render_sequence.py input.json output.svg --preset web
   ```

   Available presets are `web`, `presentation`, and `document`.

11. Render PNG only when the user or target medium needs raster output:

   ```bash
   python3 scripts/render_sequence.py input.json output.png --scale 2 --keep-svg
   ```

   SVG remains the source of truth. PNG conversion auto-detects macOS `sips`, Chrome/Chromium, `rsvg-convert`, ImageMagick, or CairoSVG.

12. Inspect the artifact at normal zoom as a business reader. Verify that every message step has a visible arrowhead pointing from sender to receiver, every numbered step explains what happens without requiring code knowledge, method names appear only as secondary text below the action, activations are short and meaningful, and branches, states, clipping, and phase ownership are correct. Fix JSON or layout settings, not generated SVG markup.
13. Deliver SVG and JSON. Include PNG only when requested or needed for embedding.

## Modeling Rules

- Preserve causal order vertically. Never reorder events merely to shorten arrows.
- Use `call` for synchronous requests, `return` for responses, `async` for fire-and-forget signals, and `self` for internal work.
- Treat a condition that changes control flow as a fragment, not a note.
- Put explanatory rules, timeouts, assumptions, or batching details in notes.
- Match the user's language in titles, phases, fragment labels, conditions, notes, participants, and business message labels.
- Model every concrete step as a message with `from`, `to`, and `label`; every message renders a directional arrow. Use notes only for explanation, never as a substitute for a step arrow.
- Put the business action in `label` and only the exact core implementation method in optional `method`. Do not combine them into an opaque English-only label.
- Omit `method` when no core method exists. Do not use it for `session_id`, `payment_id`, HTTP/SSE/JSON, event topics, state names, results, handles, or explanatory text; place those in `label`, `state`, notes, or the legend as appropriate.
- Omit activations unless the diagram specifically benefits from UML execution semantics. Keep an activation within six message positions and split nested work into shorter spans.
- Never represent a Sandbox/VM/session/database object's lifetime with an activation bar. Use explicit create/ready/destroy states or notes instead.
- Put machine states in `state`. Reserve `error` and `warning` emphasis for genuine exceptions.
- Number business-visible messages. Use explicit numbers such as `12a` and `12b` across alternative branches. Set `number: false` for support signals that would add noise.
- Put state and abbreviation definitions in the legend.
- Use participant `subtitle` for implementation identifiers while keeping participant `label` business-facing.
- Split diagrams beyond about 45 messages or 10 participants by scenario or bounded context.

## Visual Standard

- Use a white canvas, near-black text, thin strokes, dotted gray lifelines, low-saturation cards, and generous whitespace.
- Repeat participant cards at the bottom for long diagrams.
- Keep phases in a compact left rail and control-flow fragments inside subtle UML frames. Keep the first participant close to the phase rail; distribute extra canvas width between participants instead of creating large outer margins.
- Give every message kind—including calls, returns, async signals, and self-messages—the same clear filled triangular arrowhead pointing at the receiver. Distinguish returns with a dashed shaft, not an open arrowhead.
- Keep arrow shafts at high contrast, use compact arrowheads that remain obvious after PNG downsampling, and place each arrow tip directly on the receiver lifeline.
- Render the localized business action above the arrow and `method` as one smaller muted English identifier below it; the method is supporting detail, never the primary reading path.
- Treat activation bars as an advanced optional notation. A long unexplained vertical rectangle is a failed diagram.
- Use red only for states/errors and amber only for warnings.
- Prefer more width over smaller fonts. Keep normal delivery text at 11 px or larger.
- Keep the provided cross-platform font stack unless the user supplies a brand font.

## Handling Inputs

- For prose, requirements, or tables, produce a first semantic draft and state only consequential assumptions.
- For screenshots, transcribe participants, arrows, branch conditions, states, and notes before styling.
- For Mermaid or PlantUML, preserve control-flow semantics while mapping into this JSON model.
- If a requirement is ambiguous about sequence or branching, surface that ambiguity instead of silently choosing a path.

## Resources

- `scripts/render_sequence.py`: deterministic SVG renderer, strict validator, presets, and optional PNG conversion.
- `scripts/test_render_sequence.py`: dependency-free regression tests.
- `references/specification.md`: authoring guide and repair rules.
- `references/sequence.schema.json`: machine-readable JSON Schema.
- `assets/example-sequence.json`: advanced example with loops and alternatives.
