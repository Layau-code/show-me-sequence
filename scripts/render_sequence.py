#!/usr/bin/env python3
"""Render a clean, phased UML sequence diagram from a UTF-8 JSON specification."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata


DEFAULT_PARTICIPANT_COLORS = [
    "#F5F6F7", "#EAF2FB", "#EEF6ED", "#FFF3E6",
    "#F4F0F8", "#FFF8D9", "#FCECEE", "#EAF6F6",
]
DEFAULT_PHASE_COLORS = ["#EAF2FB", "#EEF6ED", "#F4F0F8", "#FFF4D8", "#FCECEE"]
MESSAGE_KINDS = {"call", "return", "async", "self"}
EVENT_KINDS = MESSAGE_KINDS | {"note", "fragment"}
FRAGMENT_OPERATORS = {"alt", "opt", "loop", "par", "break"}
EMPHASIS_VALUES = {"state", "error", "warning"}
MAX_ACTIVATION_MESSAGE_SPAN = 6
MAX_MESSAGES_PER_DIAGRAM = 28
MAX_MESSAGES_PER_PHASE = 14
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
CJK_TEXT = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
CORE_METHOD = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$-]*"
    r"(?:(?:\.|::|/|#)[A-Za-z_$][A-Za-z0-9_$-]*)*"
    r"(?:\([^()\n]{0,80}\))?$"
)
PHASE_PREFIX = re.compile(r"^\s*((?:阶段\s*\d+)|(?:Phase\s*\d+))\s*(?:[-—:：·]\s*)?(.*)$", re.IGNORECASE)
PRESETS = {
    "web": {
        "participant_spacing": 176, "participant_width": 146, "phase_rail_width": 132,
        "row_height": 56, "font_size": 13.5, "min_width": 900,
    },
    "presentation": {
        "participant_spacing": 196, "participant_width": 158, "phase_rail_width": 140,
        "row_height": 60, "font_size": 14.5, "min_width": 1500,
    },
    "document": {
        "participant_spacing": 164, "participant_width": 140, "phase_rail_width": 124,
        "row_height": 52, "font_size": 12.5, "min_width": 1040,
    },
}


class SpecError(ValueError):
    """Raised when an input specification cannot be rendered safely."""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def display_width(text: str) -> float:
    """Return a conservative font-independent width estimate in Latin character units."""
    width = 0.0
    for ch in text:
        if unicodedata.east_asian_width(ch) in "WFA":
            width += 2.0
        elif ch in "MW@#%&":
            width += 1.35
        elif ch in "ilI1.,:;'|!":
            width += 0.55
        else:
            width += 1.0
    return width


def contains_cjk(text: object) -> bool:
    return bool(CJK_TEXT.search(str(text or "")))


def split_long_token(token: str, max_units: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for ch in token:
        if current and display_width(current + ch) > max_units:
            chunks.append(current)
            current = ch
        else:
            current += ch
    if current or not chunks:
        chunks.append(current)
    return chunks


def tokenize_paragraph(text: str) -> list[str]:
    tokens: list[str] = []
    latin = ""
    for ch in text:
        if ch.isspace():
            if latin:
                tokens.append(latin)
                latin = ""
            tokens.append(" ")
        elif unicodedata.east_asian_width(ch) in "WFA":
            if latin:
                tokens.append(latin)
                latin = ""
            tokens.append(ch)
        else:
            latin += ch
    if latin:
        tokens.append(latin)
    return tokens


def wrap_text(text: object, max_units: int, max_lines: int | None = None) -> list[str]:
    """Wrap CJK, spaced Latin, and long unbroken identifiers deterministically."""
    source = str(text or "")
    lines: list[str] = []
    for paragraph in source.splitlines() or [""]:
        current = ""
        pending_space = False
        for token in tokenize_paragraph(paragraph):
            if token == " ":
                pending_space = bool(current)
                continue
            for chunk in split_long_token(token, max_units):
                prefix = " " if pending_space and current else ""
                candidate = current + prefix + chunk
                if current and display_width(candidate) > max_units:
                    lines.append(current.rstrip())
                    current = chunk
                else:
                    current = candidate
                pending_space = False
        lines.append(current.rstrip())

    lines = lines or [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while display_width(last + "…") > max_units and last:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def require_object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise SpecError(f"{path} must be an object")
    return value


def require_list(value: object, path: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list):
        raise SpecError(f"{path} must be an array")
    if nonempty and not value:
        raise SpecError(f"{path} must not be empty")
    return value


def require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{path} must be a non-empty string")
    return value.strip()


def validate_color(value: object, path: str) -> None:
    if value is not None and (not isinstance(value, str) or not HEX_COLOR.fullmatch(value)):
        raise SpecError(f"{path} must be a #RRGGBB color")


def validate_number(value: object, path: str) -> None:
    if value is None or value is False:
        return
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SpecError(f"{path} must be false, a string, or a number")


def count_message_events(events: list) -> int:
    count = 0
    for event in events:
        if event.get("kind", "call") == "fragment":
            count += sum(count_message_events(branch["events"]) for branch in event["branches"])
        elif event.get("kind", "call") in MESSAGE_KINDS:
            count += 1
    return count


def validate_events(
    events: object,
    path: str,
    known: set[str],
    event_ids: set[str],
    event_order: dict[str, int],
    order_counter: list[int],
    require_cjk_business_labels: bool,
) -> None:
    for index, raw_event in enumerate(require_list(events, path, nonempty=True)):
        event_path = f"{path}[{index}]"
        event = require_object(raw_event, event_path)
        kind = event.get("kind", "call")
        if kind not in EVENT_KINDS:
            raise SpecError(f"{event_path}.kind must be one of {sorted(EVENT_KINDS)}")
        validate_color(event.get("color"), f"{event_path}.color")

        if kind == "fragment":
            operator = event.get("operator")
            if operator not in FRAGMENT_OPERATORS:
                raise SpecError(f"{event_path}.operator must be one of {sorted(FRAGMENT_OPERATORS)}")
            if "label" in event and not isinstance(event["label"], str):
                raise SpecError(f"{event_path}.label must be a string")
            if "over" in event:
                over = require_list(event["over"], f"{event_path}.over", nonempty=True)
                if len(set(over)) != len(over) or any(pid not in known for pid in over):
                    raise SpecError(f"{event_path}.over must contain unique known participant ids")
            branches = require_list(event.get("branches"), f"{event_path}.branches", nonempty=True)
            if operator in {"alt", "par"} and len(branches) < 2:
                raise SpecError(f"{event_path}.branches requires at least two operands for {operator}")
            if operator in {"opt", "loop", "break"} and len(branches) != 1:
                raise SpecError(f"{event_path}.branches requires exactly one operand for {operator}")
            for branch_index, raw_branch in enumerate(branches):
                branch_path = f"{event_path}.branches[{branch_index}]"
                branch = require_object(raw_branch, branch_path)
                if "condition" in branch and not isinstance(branch["condition"], str):
                    raise SpecError(f"{branch_path}.condition must be a string")
                validate_events(
                    branch.get("events"), f"{branch_path}.events", known, event_ids,
                    event_order, order_counter, require_cjk_business_labels,
                )
            continue

        if kind == "note":
            over = require_list(event.get("over"), f"{event_path}.over", nonempty=True)
            if not 1 <= len(over) <= 2 or len(set(over)) != len(over) or any(pid not in known for pid in over):
                raise SpecError(f"{event_path}.over must contain one or two unique known participant ids")
            require_text(event.get("text"), f"{event_path}.text")
            continue

        source = event.get("from")
        target = event.get("to")
        if source not in known or target not in known:
            raise SpecError(f"{event_path}.from and .to must reference known participant ids")
        if kind == "self" and source != target:
            raise SpecError(f"{event_path}: kind 'self' requires from and to to be equal")
        label = require_text(event.get("label"), f"{event_path}.label")
        method = event.get("method")
        technical = event.get("technical")
        if method is not None and technical is not None:
            raise SpecError(f"{event_path} must use method or legacy technical, not both")
        if method is not None:
            method = require_text(method, f"{event_path}.method")
            if len(method) > 120 or not CORE_METHOD.fullmatch(method):
                raise SpecError(
                    f"{event_path}.method must be one concise core method name such as "
                    "createOrder or PaymentGateway.createPayment()"
                )
        if technical is not None:
            require_text(technical, f"{event_path}.technical")
        if require_cjk_business_labels and event.get("number") is not False and not contains_cjk(label):
            raise SpecError(
                f"{event_path}.label must describe the 中文业务动作; "
                "move the core method name to .method"
            )
        validate_number(event.get("number"), f"{event_path}.number")
        emphasis = event.get("emphasis")
        if emphasis is not None and emphasis not in EMPHASIS_VALUES:
            raise SpecError(f"{event_path}.emphasis must be one of {sorted(EMPHASIS_VALUES)}")
        event_id = event.get("id")
        if event_id is not None:
            event_id = require_text(event_id, f"{event_path}.id")
            if event_id in event_ids:
                raise SpecError(f"duplicate event id: {event_id}")
            event_ids.add(event_id)
            event_order[event_id] = order_counter[0]
        order_counter[0] += 1


def validate(spec: object) -> None:
    spec = require_object(spec, "root")
    require_text(spec.get("title"), "title")
    if "description" in spec and not isinstance(spec["description"], str):
        raise SpecError("description must be a string")

    raw_participants = require_list(spec.get("participants"), "participants", nonempty=True)
    if len(raw_participants) < 2:
        raise SpecError("participants must contain at least two items")
    ids: list[str] = []
    for index, raw_participant in enumerate(raw_participants):
        path = f"participants[{index}]"
        participant = require_object(raw_participant, path)
        ids.append(require_text(participant.get("id"), f"{path}.id"))
        require_text(participant.get("label"), f"{path}.label")
        if participant.get("kind", "system") not in {"actor", "system"}:
            raise SpecError(f"{path}.kind must be actor or system")
        if "subtitle" in participant and not isinstance(participant["subtitle"], str):
            raise SpecError(f"{path}.subtitle must be a string")
        validate_color(participant.get("color"), f"{path}.color")
    if len(ids) != len(set(ids)):
        raise SpecError("participant ids must be unique")

    phases = require_list(spec.get("phases"), "phases", nonempty=True)
    layout = require_object(spec.get("layout", {}), "layout")
    allow_tall = layout.get("allow_tall", False)
    if not isinstance(allow_tall, bool):
        raise SpecError("layout.allow_tall must be boolean")
    known = set(ids)
    event_ids: set[str] = set()
    event_order: dict[str, int] = {}
    order_counter = [0]
    for phase_index, raw_phase in enumerate(phases):
        path = f"phases[{phase_index}]"
        phase = require_object(raw_phase, path)
        require_text(phase.get("label"), f"{path}.label")
        validate_color(phase.get("color"), f"{path}.color")
        validate_events(
            phase.get("events"), f"{path}.events", known, event_ids,
            event_order, order_counter, contains_cjk(spec["title"]),
        )

    phase_message_counts = [count_message_events(phase["events"]) for phase in phases]
    total_messages = sum(phase_message_counts)
    if not allow_tall and (
        total_messages > MAX_MESSAGES_PER_DIAGRAM
        or any(count > MAX_MESSAGES_PER_PHASE for count in phase_message_counts)
    ):
        busiest_phase = max(phase_message_counts, default=0)
        raise SpecError(
            "readability budget exceeded "
            f"({total_messages} messages total; busiest phase has {busiest_phase}); "
            "split the flow into one overview and focused detail diagrams, or set "
            "layout.allow_tall=true only when a single audit diagram is explicitly required"
        )

    activations = require_list(spec.get("activations", []), "activations")
    for index, raw_activation in enumerate(activations):
        path = f"activations[{index}]"
        activation = require_object(raw_activation, path)
        if activation.get("participant") not in known:
            raise SpecError(f"{path}.participant must reference a known participant")
        start, end = activation.get("from"), activation.get("to")
        if start not in event_ids or end not in event_ids:
            raise SpecError(f"{path}.from and .to must reference message event ids")
        if event_order[start] > event_order[end]:
            raise SpecError(f"{path}.from must occur before or at .to")
        span = event_order[end] - event_order[start] + 1
        if span > MAX_ACTIVATION_MESSAGE_SPAN:
            raise SpecError(
                f"{path}: activation is too long ({span} messages; maximum "
                f"{MAX_ACTIVATION_MESSAGE_SPAN}); split it into short synchronous work spans or omit it"
            )

    legend = require_list(spec.get("legend", []), "legend")
    for index, raw_item in enumerate(legend):
        path = f"legend[{index}]"
        item = require_object(raw_item, path)
        require_text(item.get("term"), f"{path}.term")
        require_text(item.get("text"), f"{path}.text")

    preset = layout.get("preset", "web")
    if preset not in PRESETS:
        raise SpecError(f"layout.preset must be one of {sorted(PRESETS)}")
    numeric_layout = {
        "participant_spacing": (120, 400), "participant_width": (90, 260),
        "phase_rail_width": (90, 240), "row_height": (44, 140),
        "font_size": (11, 22), "min_width": (640, 10000),
    }
    for key, (minimum, maximum) in numeric_layout.items():
        if key in layout:
            value = layout[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                raise SpecError(f"layout.{key} must be a number between {minimum} and {maximum}")
    if "show_bottom_participants" in layout and not isinstance(layout["show_bottom_participants"], bool):
        raise SpecError("layout.show_bottom_participants must be boolean")

    theme = require_object(spec.get("theme", {}), "theme")
    for key in ("text", "muted", "line", "lifeline", "border", "state", "warning", "background", "note", "activation"):
        validate_color(theme.get(key), f"theme.{key}")
    if "font_family" in theme and not isinstance(theme["font_family"], str):
        raise SpecError("theme.font_family must be a string")
    labels = require_object(spec.get("labels", {}), "labels")
    if "legend" in labels and not isinstance(labels["legend"], str):
        raise SpecError("labels.legend must be a string")


def text_svg(
    x: float,
    y: float,
    lines: list[str],
    *,
    size: float,
    fill: str,
    anchor: str = "middle",
    weight: int = 400,
    line_height: float | None = None,
    data_role: str | None = None,
) -> str:
    line_height = line_height or size * 1.32
    first_y = y - (len(lines) - 1) * line_height / 2
    attrs = (
        f'x="{x:.1f}" y="{first_y:.1f}" text-anchor="{anchor}" '
        f'font-size="{size}" font-weight="{weight}" fill="{esc(fill)}"'
    )
    if data_role:
        attrs += f' data-role="{esc(data_role)}"'
    tspans = [
        f'<tspan x="{x:.1f}" dy="{"0" if index == 0 else f"{line_height:.1f}"}">{esc(line)}</tspan>'
        for index, line in enumerate(lines)
    ]
    return f"<text {attrs}>{''.join(tspans)}</text>"


def participant_lines(participant: dict, width: float) -> tuple[list[str], list[str]]:
    label = str(participant["label"])
    subtitle = str(participant.get("subtitle", "")).strip()
    label_units = max(8, int(width / 7.5))
    subtitle_units = max(8, int(width / 6.8))
    return (
        wrap_text(label, label_units),
        wrap_text(f"({subtitle})", subtitle_units) if subtitle else [],
    )


def participant_card_height(participants: list[dict], width: float) -> float:
    heights: list[float] = []
    for participant in participants:
        label_lines, subtitle_lines = participant_lines(participant, width)
        if participant.get("kind") == "actor":
            heights.append(max(70, 52 + len(label_lines) * 15))
        else:
            heights.append(max(70, 24 + len(label_lines) * 15 + len(subtitle_lines) * 13))
    return max(heights, default=70)


def participant_card(
    participant: dict,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    text_color: str,
    muted: str,
    border: str,
) -> str:
    parts = [
        f'<rect x="{x - width / 2:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'rx="7" fill="{esc(fill)}" stroke="{esc(border)}"/>'
    ]
    label_lines, subtitle_lines = participant_lines(participant, width)
    if participant.get("kind") == "actor":
        cx, cy = x, y + 17
        parts.extend([
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="none" stroke="{esc(text_color)}" stroke-width="1.2"/>',
            f'<path d="M {cx:.1f} {cy + 5:.1f} V {cy + 17:.1f} M {cx - 8:.1f} {cy + 10:.1f} H {cx + 8:.1f} '
            f'M {cx:.1f} {cy + 17:.1f} L {cx - 7:.1f} {cy + 25:.1f} M {cx:.1f} {cy + 17:.1f} L {cx + 7:.1f} {cy + 25:.1f}" '
            f'fill="none" stroke="{esc(text_color)}" stroke-width="1.2" stroke-linecap="round"/>',
            text_svg(
                x, y + height - 12 - (len(label_lines) - 1) * 7.5,
                label_lines, size=12.8, fill=text_color, weight=700, line_height=15,
            ),
        ])
    else:
        subtitle_block = len(subtitle_lines) * 13
        label_y = y + height / 2 - (subtitle_block + (5 if subtitle_lines else 0)) / 2
        parts.append(text_svg(x, label_y, label_lines, size=12.8, fill=text_color, weight=700, line_height=15))
        if subtitle_lines:
            subtitle_y = y + height / 2 + (len(label_lines) * 15 + 5) / 2
            parts.append(text_svg(x, subtitle_y, subtitle_lines, size=10.5, fill=muted, line_height=13))
    return "".join(parts)


def layout_events(
    events: list,
    y: float,
    row_height: float,
    event_y: dict[str, float],
    counter: list[int],
    xs: dict[str, float],
    spacing: float,
    font_size: float,
) -> tuple[list[dict], float]:
    nodes: list[dict] = []
    for event in events:
        kind = event.get("kind", "call")
        if kind == "fragment":
            top = y
            cursor = y + 30
            branches = []
            for branch in event["branches"]:
                branch_top = cursor
                cursor += 24
                children, cursor = layout_events(
                    branch["events"], cursor, row_height, event_y, counter,
                    xs, spacing, font_size,
                )
                cursor += 10
                branches.append({"branch": branch, "top": branch_top, "bottom": cursor, "nodes": children})
            bottom = cursor + 8
            nodes.append({"event": event, "kind": kind, "top": top, "bottom": bottom, "branches": branches})
            y = bottom + 12
            continue

        number: str | None = None
        label_lines: list[str] = []
        method_lines: list[str] = []
        state_lines: list[str] = []
        label_width = 0.0
        if kind in MESSAGE_KINDS:
            supplied = event.get("number")
            if supplied is False:
                number = None
            elif supplied is not None:
                number = str(supplied)
            else:
                counter[0] += 1
                number = str(counter[0])
            source, target = str(event["from"]), str(event["to"])
            if source == target:
                loop_width = min(60, spacing * 0.34)
                label_width = loop_width + 90
            else:
                label_width = max(130, abs(xs[target] - xs[source]) - 18)
            label = str(event["label"])
            if number:
                label = f"{number}. {label}"
            label_lines = wrap_text(label, max(14, int(label_width / (font_size * 0.53))))
            method_text = str(event.get("method") or event.get("technical") or "").strip()
            if method_text:
                method_lines = wrap_text(method_text, max(14, int(label_width / 6.0)))
            state = str(event.get("state", "")).strip()
            if state:
                state_lines = wrap_text(state, max(14, int(label_width / 7)))
            if event.get("id"):
                event_y[str(event["id"])] = 0

        if kind == "note":
            height = row_height + 10
        elif kind in MESSAGE_KINDS:
            label_block = len(label_lines) * font_size * 1.25
            detail_block = len(method_lines) * 13 + len(state_lines) * 14
            height = max(row_height, 24 + label_block + detail_block)
        else:
            height = row_height
        center = y + height / 2
        if kind in MESSAGE_KINDS and event.get("id"):
            event_y[str(event["id"])] = center
        nodes.append({
            "event": event, "kind": kind, "top": y, "bottom": y + height,
            "center": center, "number": number, "label_lines": label_lines,
            "method_lines": method_lines, "state_lines": state_lines,
            "label_width": label_width,
        })
        y += height
    return nodes, y


def walk_nodes(nodes: list[dict]):
    for node in nodes:
        yield node
        for branch in node.get("branches", []):
            yield from walk_nodes(branch["nodes"])


def fragment_participants(event: dict) -> set[str]:
    if event.get("over"):
        return set(event["over"])
    participants: set[str] = set()
    for branch in event["branches"]:
        for child in branch["events"]:
            kind = child.get("kind", "call")
            if kind == "fragment":
                participants.update(fragment_participants(child))
            elif kind == "note":
                participants.update(child["over"])
            else:
                participants.update((child["from"], child["to"]))
    return participants


def event_color(event: dict, colors: dict[str, str]) -> str:
    if event.get("color"):
        return event["color"]
    if event.get("emphasis") in {"state", "error"}:
        return colors["state"]
    if event.get("emphasis") == "warning":
        return colors["warning"]
    return colors["line"]


def phase_label_parts(label: object, index: int, cjk: bool) -> tuple[str, str]:
    source_lines = [line.strip() for line in str(label).splitlines() if line.strip()]
    source = source_lines[0] if source_lines else ""
    match = PHASE_PREFIX.match(source)
    if match:
        number = re.sub(r"\s+", "", match.group(1)) if contains_cjk(match.group(1)) else match.group(1)
        name_parts = [match.group(2).strip(), *source_lines[1:]]
        return number, " ".join(part for part in name_parts if part)
    number = f"阶段{index + 1}" if cjk else f"Phase {index + 1}"
    return number, " ".join(source_lines)


def arrowhead_svg(
    tip_x: float,
    tip_y: float,
    direction: int,
    color: str,
    source: str,
    target: str,
    step: object,
) -> str:
    """Draw an explicit filled arrowhead so all SVG/PNG converters preserve it."""
    depth, half_height = 9.0, 4.5
    base_x = tip_x - direction * depth
    points = f"{tip_x:.1f},{tip_y:.1f} {base_x:.1f},{tip_y - half_height:.1f} {base_x:.1f},{tip_y + half_height:.1f}"
    return (
        f'<polygon data-role="step-arrowhead" data-step="{esc(step)}" '
        f'data-from="{esc(source)}" data-to="{esc(target)}" points="{points}" fill="{esc(color)}"/>'
    )


def render_fragment_frames(
    nodes: list[dict],
    xs: dict[str, float],
    card_width: float,
    phase_width: float,
    width: float,
    colors: dict[str, str],
) -> list[str]:
    svg: list[str] = []
    for node in nodes:
        if node["kind"] != "fragment":
            continue
        event = node["event"]
        involved = fragment_participants(event)
        involved_x = [xs[pid] for pid in involved]
        left = max(phase_width + 10, min(involved_x) - card_width * 0.48)
        right = min(width - 18, max(involved_x) + card_width * 0.48)
        if right - left < 220:
            center = (left + right) / 2
            left = max(phase_width + 10, center - 110)
            right = min(width - 18, center + 110)
        top, bottom = node["top"], node["bottom"]
        svg.append(
            f'<rect x="{left:.1f}" y="{top:.1f}" width="{right - left:.1f}" height="{bottom - top:.1f}" '
            f'fill="#FFFFFF" fill-opacity="0.88" stroke="{esc(colors["border"])}" stroke-width="1.4"/>'
        )
        operator_text = event["operator"]
        if str(event.get("label", "")).strip():
            operator_text += f' · {event["label"]}'
        tab_width = min(right - left - 20, max(76, 16 + display_width(operator_text) * 7.4))
        tab_lines = wrap_text(operator_text, max(8, int(tab_width / 7.4)))
        tab_height = 25 + max(0, len(tab_lines) - 1) * 15
        svg.append(
            f'<path d="M {left:.1f} {top:.1f} H {left + tab_width:.1f} L {left + tab_width - 10:.1f} {top + tab_height:.1f} '
            f'H {left:.1f} Z" fill="#F3F4F5" stroke="{esc(colors["border"])}" stroke-width="1"/>'
        )
        svg.append(text_svg(left + 9, top + 17 + (len(tab_lines) - 1) * 7.5, tab_lines, size=12.5, fill=colors["text"], anchor="start", weight=700, line_height=15))
        for index, branch in enumerate(node["branches"]):
            if index > 0:
                svg.append(
                    f'<line x1="{left:.1f}" y1="{branch["top"]:.1f}" x2="{right:.1f}" y2="{branch["top"]:.1f}" '
                    f'stroke="{esc(colors["border"])}" stroke-width="1" stroke-dasharray="5 4"/>'
                )
            condition = str(branch["branch"].get("condition", "")).strip()
            if condition:
                condition_text = condition if condition.startswith("[") else f"[{condition}]"
                svg.append(text_svg(left + 9, branch["top"] + 16, wrap_text(condition_text, max(16, int((right - left - 18) / 7))), size=12, fill=colors["text"], anchor="start", weight=600, line_height=15))
            svg.extend(render_fragment_frames(branch["nodes"], xs, card_width, phase_width, width, colors))
    return svg


def render_event_nodes(
    nodes: list[dict],
    xs: dict[str, float],
    width: float,
    phase_width: float,
    card_width: float,
    spacing: float,
    font_size: float,
    colors: dict[str, str],
) -> list[str]:
    svg: list[str] = []
    for node in nodes:
        if node["kind"] == "fragment":
            for branch in node["branches"]:
                svg.extend(render_event_nodes(branch["nodes"], xs, width, phase_width, card_width, spacing, font_size, colors))
            continue
        event = node["event"]
        center = node["center"]
        if node["kind"] == "note":
            over = event["over"]
            x1, x2 = xs[over[0]], xs[over[-1]]
            note_width = max(180, abs(x2 - x1) + card_width * 0.72)
            note_width = min(note_width, width - phase_width - 54)
            note_x = min(width - 20 - note_width, max(phase_width + 16, (x1 + x2) / 2 - note_width / 2))
            note_lines = wrap_text(event["text"], max(20, int(note_width / 7.2)), 4)
            note_h = max(40, 16 + len(note_lines) * 16)
            note_y = center - note_h / 2
            svg.append(
                f'<rect x="{note_x:.1f}" y="{note_y:.1f}" width="{note_width:.1f}" height="{note_h:.1f}" rx="5" '
                f'fill="{esc(event.get("color", colors["note"]))}" stroke="{esc(colors["border"])}" stroke-dasharray="3 3"/>'
            )
            svg.append(text_svg(note_x + note_width / 2, center, note_lines, size=12, fill=colors["muted"], line_height=16))
            continue

        source, target = str(event["from"]), str(event["to"])
        x1, x2 = xs[source], xs[target]
        color = event_color(event, colors)
        kind = node["kind"]
        dash = ' stroke-dasharray="6 4"' if kind == "return" else ""
        step = event.get("id", node["number"] or "")
        arrow_meta = (
            f'data-role="step-arrow" data-step="{esc(step)}" '
            f'data-from="{esc(source)}" data-to="{esc(target)}"'
        )
        if source == target:
            loop_width = min(60, spacing * 0.34)
            path = f"M {x1:.1f} {center:.1f} h {loop_width:.1f} v 27 h {-loop_width:.1f}"
            svg.append(
                f'<path {arrow_meta} d="{path}" fill="none" stroke="{esc(color)}" stroke-width="2"{dash} '
                f'stroke-linejoin="round"/>'
            )
            svg.append(arrowhead_svg(x1, center + 27, -1, color, source, target, step))
            label_x = x1 + loop_width / 2
        else:
            direction = 1 if x2 > x1 else -1
            start, end = x1, x2
            svg.append(
                f'<line {arrow_meta} x1="{start:.1f}" y1="{center:.1f}" x2="{end:.1f}" y2="{center:.1f}" '
                f'stroke="{esc(color)}" stroke-width="2"{dash}/>'
            )
            svg.append(arrowhead_svg(end, center, direction, color, source, target, step))
            label_x = (x1 + x2) / 2
        label_lines = node["label_lines"]
        method_lines = node["method_lines"]
        state_lines = node["state_lines"]
        label_line_height = font_size * 1.25
        label_y = center - 12 - (len(label_lines) - 1) * label_line_height / 2
        svg.append(text_svg(label_x, label_y, label_lines, size=font_size, fill=color, weight=500, line_height=font_size * 1.25))
        if method_lines:
            svg.append(text_svg(label_x, center + 16, method_lines, size=10.5, fill=colors["muted"], weight=400, line_height=13))
        if state_lines:
            state_y = center + (34 if method_lines else 18) + max(0, len(method_lines) - 1) * 13
            svg.append(text_svg(label_x, state_y, state_lines, size=11.5, fill=colors["state"], weight=600, line_height=14))
    return svg


def render(spec: dict, preset_override: str | None = None) -> tuple[str, int, int]:
    validate(spec)
    raw_layout = spec.get("layout", {})
    preset = preset_override or raw_layout.get("preset", "web")
    if preset not in PRESETS:
        raise SpecError(f"preset must be one of {sorted(PRESETS)}")
    layout = dict(PRESETS[preset])
    layout.update({key: value for key, value in raw_layout.items() if key != "preset"})
    theme = spec.get("theme", {})
    spacing = float(layout["participant_spacing"])
    card_width = float(layout["participant_width"])
    phase_width = float(layout["phase_rail_width"])
    row_height = float(layout["row_height"])
    font_size = float(layout["font_size"])
    min_width = int(layout["min_width"])
    show_bottom = bool(layout.get("show_bottom_participants", True))

    colors = {
        "text": theme.get("text", "#17191C"),
        "muted": theme.get("muted", "#62676E"),
        "line": theme.get("line", "#202326"),
        "lifeline": theme.get("lifeline", "#A7ADB4"),
        "border": theme.get("border", "#9299A1"),
        "state": theme.get("state", "#D94242"),
        "warning": theme.get("warning", "#B87510"),
        "background": theme.get("background", "#FFFFFF"),
        "note": theme.get("note", "#F7F7F5"),
        "activation": theme.get("activation", "#F4F5F6"),
    }
    font_family = theme.get("font_family", "Inter, Noto Sans SC, PingFang SC, Microsoft YaHei, Arial, sans-serif")

    participants = spec["participants"]
    first_x = phase_width + 10 + card_width / 2
    xs = {participant["id"]: first_x + index * spacing for index, participant in enumerate(participants)}
    natural_width = int(first_x + (len(participants) - 1) * spacing + card_width / 2 + 40)
    width = max(min_width, natural_width)
    if width > natural_width and len(participants) > 1:
        expanded_spacing = spacing + (width - natural_width) / (len(participants) - 1)
        xs = {
            participant["id"]: first_x + index * expanded_spacing
            for index, participant in enumerate(participants)
        }

    title_lines = wrap_text(spec["title"], max(36, int((width - 80) / 9)))
    title_center_y = 24 + (len(title_lines) - 1) * 12
    card_y = 22 + len(title_lines) * 24 + 18
    card_height = participant_card_height(participants, card_width)
    timeline_top = card_y + card_height + 18
    y = timeline_top
    event_y: dict[str, float] = {}
    phase_layouts: list[dict] = []
    counter = [0]
    for phase in spec["phases"]:
        phase_top = y
        y += 30
        nodes, y = layout_events(
            phase["events"], y, row_height, event_y, counter,
            xs, spacing, font_size,
        )
        y += 18
        phase_layouts.append({"phase": phase, "top": phase_top, "bottom": y, "nodes": nodes})
    timeline_bottom = y

    legend_lines: list[str] = []
    if spec.get("legend"):
        legend_max_units = max(42, int((width - phase_width - 100) / 7.2))
        for item in spec["legend"]:
            legend_lines.extend(wrap_text(f'{item["term"]}: {item["text"]}', legend_max_units))
    legend_height = 26 + 18 * len(legend_lines) if legend_lines else 0
    bottom_card_y = timeline_bottom + legend_height + 28
    height = int(bottom_card_y + (card_height if show_bottom else 0) + 24)

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f'<title>{esc(spec["title"])}</title>',
        f'<desc>{esc(spec.get("description", "Phased UML sequence diagram"))}</desc>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{esc(colors["background"])}"/>',
        f'<g font-family="{esc(font_family)}" shape-rendering="geometricPrecision" text-rendering="geometricPrecision">',
        text_svg(width / 2, title_center_y, title_lines, size=18, fill=colors["text"], weight=700, line_height=23),
    ]

    for index, participant in enumerate(participants):
        fill = participant.get("color", DEFAULT_PARTICIPANT_COLORS[index % len(DEFAULT_PARTICIPANT_COLORS)])
        svg.append(participant_card(participant, xs[participant["id"]], card_y, card_width, card_height, fill, colors["text"], colors["muted"], colors["border"]))

    lifeline_end = bottom_card_y if show_bottom else timeline_bottom + legend_height + 10
    for participant in participants:
        x = xs[participant["id"]]
        svg.append(
            f'<line data-role="lifeline" data-participant="{esc(participant["id"])}" '
            f'x1="{x:.1f}" y1="{timeline_top - 6:.1f}" x2="{x:.1f}" y2="{lifeline_end:.1f}" '
            f'stroke="{esc(colors["lifeline"])}" stroke-width="1" stroke-dasharray="4 5"/>'
        )

    for index, phase_layout in enumerate(phase_layouts):
        phase, top, bottom = phase_layout["phase"], phase_layout["top"], phase_layout["bottom"]
        if index > 0:
            svg.append(
                f'<line x1="16" y1="{top:.1f}" x2="{width - 16}" y2="{top:.1f}" '
                f'stroke="{esc(colors["lifeline"])}" stroke-width="1" stroke-dasharray="2 4"/>'
            )
        fill = phase.get("color", DEFAULT_PHASE_COLORS[index % len(DEFAULT_PHASE_COLORS)])
        phase_number, phase_name = phase_label_parts(phase["label"], index, contains_cjk(spec["title"]))
        phase_name_lines = wrap_text(phase_name, max(8, int((phase_width - 34) / 7))) if phase_name else []
        desired_box_h = 38 + len(phase_name_lines) * 17
        box_y = top + 12
        box_h = max(62, min(92, desired_box_h, bottom - top - 22))
        svg.append(
            f'<rect data-role="phase-card" x="12" y="{box_y:.1f}" width="{phase_width - 20:.1f}" height="{box_h:.1f}" '
            f'fill="{esc(fill)}" stroke="{esc(colors["border"])}" stroke-width="1.2"/>'
        )
        phase_center_x = 12 + (phase_width - 20) / 2
        if phase_name_lines:
            svg.append(text_svg(phase_center_x, box_y + 20, [phase_number], size=12.5, fill=colors["text"], weight=700, data_role="phase-number"))
            phase_name_y = box_y + 43 + (len(phase_name_lines) - 1) * 8.5
            svg.append(text_svg(phase_center_x, phase_name_y, phase_name_lines, size=13.2, fill=colors["text"], weight=700, line_height=17, data_role="phase-name"))
        else:
            svg.append(text_svg(phase_center_x, box_y + box_h / 2, [phase_number], size=13.2, fill=colors["text"], weight=700, data_role="phase-number"))
        svg.extend(render_fragment_frames(phase_layout["nodes"], xs, card_width, phase_width, width, colors))

    for activation in spec.get("activations", []):
        x = xs[activation["participant"]]
        y1, y2 = event_y[activation["from"]] - 15, event_y[activation["to"]] + 17
        svg.append(
            f'<rect x="{x - 5:.1f}" y="{y1:.1f}" width="10" height="{max(16, y2 - y1):.1f}" '
            f'fill="{esc(colors["activation"])}" stroke="{esc(colors["line"])}" stroke-width="1"/>'
        )

    for phase_layout in phase_layouts:
        svg.extend(render_event_nodes(phase_layout["nodes"], xs, width, phase_width, card_width, spacing, font_size, colors))

    if legend_lines:
        legend_x, legend_y = phase_width + 22, timeline_bottom + 16
        legend_width, legend_box_h = width - (phase_width + 22) - 28, legend_height - 6
        svg.append(
            f'<rect x="{legend_x:.1f}" y="{legend_y:.1f}" width="{legend_width:.1f}" height="{legend_box_h:.1f}" '
            f'fill="#FAFAF9" stroke="{esc(colors["border"])}"/>'
        )
        labels = spec.get("labels", {})
        legend_title = labels.get("legend")
        if legend_title is None:
            legend_title = "说明" if any(unicodedata.east_asian_width(ch) in "WFA" for ch in spec["title"]) else "Legend"
        svg.append(text_svg(legend_x + 10, legend_y + 16, [legend_title], size=11.5, fill=colors["text"], anchor="start", weight=700))
        line_y = legend_y + 34
        for line in legend_lines:
            svg.append(text_svg(legend_x + 10, line_y, [line], size=11, fill=colors["muted"], anchor="start"))
            line_y += 18

    if show_bottom:
        for index, participant in enumerate(participants):
            fill = participant.get("color", DEFAULT_PARTICIPANT_COLORS[index % len(DEFAULT_PARTICIPANT_COLORS)])
            svg.append(participant_card(participant, xs[participant["id"]], bottom_card_y, card_width, card_height, fill, colors["text"], colors["muted"], colors["border"]))

    svg.extend(["</g>", "</svg>"])
    return "\n".join(svg), width, height


def find_chrome() -> str | None:
    candidates = [
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        os.path.expandvars(r"$PROGRAMFILES\Google\Chrome\Application\chrome.exe"),
    ]
    return next((str(path) for path in candidates if path and Path(path).exists()), None)


def convert_png(svg_path: Path, png_path: Path, width: int, height: int, scale: float) -> None:
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run([rsvg, "-w", str(round(width * scale)), "-h", str(round(height * scale)), "-o", str(png_path), str(svg_path)], check=True)
        return
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        subprocess.run([magick, "-density", str(round(96 * scale)), str(svg_path), str(png_path)], check=True)
        return
    try:
        import cairosvg  # type: ignore
        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), output_width=round(width * scale), output_height=round(height * scale))
        return
    except ImportError:
        pass
    sips = shutil.which("sips")
    if sips:
        subprocess.run([
            sips, "-s", "format", "png", "--resampleHeightWidth",
            str(round(height * scale)), str(round(width * scale)), str(svg_path), "--out", str(png_path),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    chrome = find_chrome()
    if chrome:
        with tempfile.TemporaryDirectory(prefix="sequence-chrome-") as profile_dir:
            subprocess.run([
                chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--disable-extensions", "--run-all-compositor-stages-before-draw",
                f"--user-data-dir={profile_dir}", f"--force-device-scale-factor={scale}",
                f"--window-size={width},{height}", f"--screenshot={png_path.resolve()}", svg_path.resolve().as_uri(),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return
    raise RuntimeError("PNG export needs macOS sips, Chrome/Chromium, rsvg-convert, ImageMagick, or CairoSVG; SVG was generated successfully")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 JSON specification")
    parser.add_argument("output", type=Path, nargs="?", help="output .svg or .png path")
    parser.add_argument("--scale", type=float, default=2.0, help="PNG scale factor (default: 2)")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="override layout preset")
    parser.add_argument("--check", action="store_true", help="validate input without rendering")
    parser.add_argument("--keep-svg", action="store_true", help="keep an SVG beside PNG output")
    args = parser.parse_args()
    if args.scale <= 0:
        parser.error("--scale must be positive")
    if not args.check and args.output is None:
        parser.error("output is required unless --check is used")
    if args.output and args.output.suffix.lower() not in {".svg", ".png"}:
        parser.error("output extension must be .svg or .png")

    try:
        with args.input.open("r", encoding="utf-8") as handle:
            spec = json.load(handle)
        validate(spec)
        if args.check:
            print(f"Valid specification: {args.input}")
            return 0
        assert args.output is not None
        svg, width, height = render(spec, args.preset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.suffix.lower() == ".svg":
            args.output.write_text(svg, encoding="utf-8")
        elif args.keep_svg:
            svg_path = args.output.with_suffix(".svg")
            svg_path.write_text(svg, encoding="utf-8")
            convert_png(svg_path, args.output, width, height, args.scale)
        else:
            with tempfile.TemporaryDirectory(prefix="sequence-diagram-") as temp_dir:
                svg_path = Path(temp_dir) / "diagram.svg"
                svg_path.write_text(svg, encoding="utf-8")
                convert_png(svg_path, args.output, width, height, args.scale)
        print(f"Rendered {args.output} ({width}x{height} logical pixels, preset={args.preset or spec.get('layout', {}).get('preset', 'web')})")
        return 0
    except (OSError, json.JSONDecodeError, SpecError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
