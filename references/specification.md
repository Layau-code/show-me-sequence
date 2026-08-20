# Sequence specification

## Contents

- [Minimal shape](#minimal-shape)
- [Participants](#participants)
- [Messages and notes](#messages-and-notes)
- [Control-flow fragments](#control-flow-fragments)
- [Activations](#activations)
- [Legend, labels, and themes](#legend-labels-and-themes)
- [Layout presets](#layout-presets)
- [Validation and repair](#validation-and-repair)

## Minimal shape

Use UTF-8 JSON. Validate with `python3 scripts/render_sequence.py input.json --check`. A machine-readable schema is available at [sequence.schema.json](sequence.schema.json).

```json
{
  "title": "Account recovery flow",
  "participants": [
    {"id": "user", "label": "User", "kind": "actor"},
    {"id": "app", "label": "Web app"},
    {"id": "identity", "label": "Identity service", "subtitle": "IdentityService"}
  ],
  "phases": [
    {
      "label": "Phase 1\nRequest",
      "events": [
        {"id": "m1", "from": "user", "to": "app", "label": "Request password reset"},
        {"id": "m2", "from": "app", "to": "identity", "label": "Create reset challenge"},
        {"id": "m3", "from": "identity", "to": "app", "label": "Challenge created", "kind": "return", "state": "CHALLENGE_PENDING"}
      ]
    }
  ]
}
```

## Participants

- `id` (required): stable identifier used by events.
- `label` (required): business-facing name.
- `subtitle`: class, service, bounded context, or implementation name.
- `kind`: `actor` or `system` (default).
- `color`: optional pale `#RRGGBB` card fill.

Order participants from initiator to deepest dependency. Participants may represent people, organizations, applications, services, stores, queues, devices, or external providers.

## Messages and notes

Message fields:

- `id`: recommended; required when referenced by an activation.
- `from`, `to`: participant IDs. Equal IDs draw a self-message.
- `label`: concise action or result.
- `method`: optional single core method name rendered as smaller muted text beneath `label`, such as `createOrder`, `PaymentGateway.createPayment()`, or `Sandbox.create(scope)`.
- `technical`: legacy alias retained for old specifications. Use `method` for new diagrams; never set both.
- `kind`: `call` (solid), `return` (dashed), `async` (solid), or `self`. Every kind uses a prominent filled triangular arrowhead pointing at the receiver; line style carries the semantic distinction.
- `state`: optional machine state rendered below the arrow.
- `number`: omit for automatic numbering, use a string/number for `12a`, or `false` to suppress.
- `color`: optional `#RRGGBB` arrow and arrowhead color.
- `emphasis`: `state`, `error`, or `warning`.

For `kind: self`, `from` and `to` must be equal.

Follow the diagram's language for business labels. For a Chinese title, every numbered/business-visible message must use a Chinese action phrase. Put only the core method name into `method`:

```json
{
  "from": "runtime",
  "to": "cloud",
  "label": "创建远程沙箱",
  "method": "Sandbox.create(scope)"
}
```

Avoid `"label": "Sandbox.create"`: it tells an implementation-aware reader what method ran, but not a business reader what happened. Also avoid `"method": "create sandbox and return handle"`; `method` is not a description. Omit it when no true method exists. Fields, protocols, event topics, states, return values, and handles belong in the business label, state, note, or legend. Unnumbered support signals (`number: false`) may remain protocol-only when translation would add no meaning.

Every message event renders an arrow from `from` to `to`, including self-messages. The localized business `label` appears above the shaft, and the optional English core `method` appears below it. Notes and fragment frames are explanatory structure and are never numbered steps. If something is a real step, model it as a message rather than a note.

Note example:

```json
{"kind": "note", "over": ["app", "identity"], "text": "Token expires after 15 minutes"}
```

`over` accepts one or two participant IDs. Notes explain behavior but do not change control flow or receive step numbers.

## Control-flow fragments

Use `fragment` for UML combined fragments. Nested fragments are supported.

```json
{
  "kind": "fragment",
  "operator": "alt",
  "label": "Verification result",
  "over": ["app", "identity"],
  "branches": [
    {
      "condition": "token valid",
      "events": [
        {"from": "identity", "to": "app", "label": "Grant access", "number": "4a"}
      ]
    },
    {
      "condition": "token expired",
      "events": [
        {"from": "identity", "to": "app", "label": "Reject access", "number": "4b", "emphasis": "error"}
      ]
    }
  ]
}
```

Operators:

- `alt`: two or more mutually exclusive branches.
- `par`: two or more concurrent operands, displayed as UML operands rather than implying sequential dependence.
- `opt`: exactly one conditional branch.
- `loop`: exactly one repeated branch; put retry count or exit rule in `label` or `condition`.
- `break`: exactly one branch that terminates the surrounding interaction.

`over` is optional. When omitted, the frame spans participants found recursively inside its branches.

## Activations

```json
{
  "activations": [
    {"participant": "identity", "from": "m2", "to": "m3"}
  ]
}
```

`from` and `to` reference message IDs in causal order. Activations are optional and normally omitted. Use one only for a short synchronous execution span of at most six message positions.

Do not use an activation to represent:

- the lifetime of a Sandbox, VM, session, database record, handle, or other resource;
- ownership or availability;
- an entire request, phase, branch, or background service lifespan.

Represent these concepts with explicit create/ready/release/destroy messages, `state`, or a note. Long activations are rejected because they appear as unexplained vertical bars and visually dominate the diagram.

## Legend, labels, and themes

```json
{
  "legend": [
    {"term": "CHALLENGE_PENDING", "text": "Challenge created; verification pending"}
  ],
  "labels": {"legend": "Legend"}
}
```

The legend title defaults to `说明` for CJK titles and `Legend` otherwise. Override it through `labels.legend`.

Theme colors must use `#RRGGBB`. Supported keys are `text`, `muted`, `line`, `lifeline`, `border`, `state`, `warning`, `background`, `note`, and `activation`. `font_family` accepts an SVG font-family string.

## Layout presets

Set `layout.preset` or pass `--preset` on the CLI:

- `web`: balanced default for browser viewing.
- `presentation`: larger type and spacing for 16:9 slides.
- `document`: denser layout for reports and PDF pages.

All presets keep the phase rail and outside margins compact. When `min_width` exceeds the natural diagram width, the renderer distributes that extra room between participants rather than adding large blank margins.

Optional overrides:

- `participant_spacing`: `120`–`400`.
- `participant_width`: `90`–`260`.
- `phase_rail_width`: `90`–`240`.
- `row_height`: `44`–`140`.
- `font_size`: `11`–`22`.
- `min_width`: `640`–`10000`.
- `show_bottom_participants`: boolean.
- `allow_tall`: boolean. Defaults to `false`. Set to `true` only when the user explicitly requires one audit-style diagram instead of a readable overview and focused details.

The default readability budget is 28 messages per diagram and 14 messages per phase, including messages nested in fragments. When either limit is exceeded, split the source into:

1. One overview diagram showing the end-to-end business path.
2. Focused detail diagrams for dense phases, exception paths, or bounded contexts.

Do not abbreviate business actions or participant names with an ellipsis to force them into one canvas. The renderer wraps them completely and expands row or card height as needed.

## Validation and repair

The renderer rejects malformed structures, duplicate IDs, unknown participants, invalid fragments, backward or overly long activations, unsupported emphasis, unsafe layout ranges, invalid colors, and English-only business steps in a Chinese diagram.

If labels collide:

1. Shorten action labels and move definitions into the legend.
2. Increase participant spacing or select `presentation`.
3. Split the scenario or bounded context.
4. Reduce font size only as a last resort; never below 11.

If a diagram is too tall, remove redundant acknowledgements or split happy and exception paths. Do not flatten genuine alternatives or parallel behavior into a false linear sequence. Use `layout.allow_tall: true` only for an explicitly requested single audit diagram.
