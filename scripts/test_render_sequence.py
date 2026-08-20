#!/usr/bin/env python3
"""Regression tests for render_sequence.py. Uses only the Python standard library."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
MODULE_SPEC = importlib.util.spec_from_file_location("render_sequence", SCRIPT_DIR / "render_sequence.py")
assert MODULE_SPEC and MODULE_SPEC.loader
renderer = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(renderer)


def minimal_spec(events: list[dict] | None = None) -> dict:
    return {
        "title": "Test flow",
        "participants": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "phases": [{"label": "Phase", "events": events or [{"id": "m1", "from": "a", "to": "b", "label": "Call"}]}],
    }


class RendererTests(unittest.TestCase):
    def test_chinese_diagram_rejects_english_only_business_step(self) -> None:
        spec = minimal_spec([{"id": "m1", "from": "a", "to": "b", "label": "Sandbox.create"}])
        spec["title"] = "请求级沙箱生命周期"
        with self.assertRaisesRegex(renderer.SpecError, "中文业务动作"):
            renderer.validate(spec)

    def test_core_method_renders_below_business_label(self) -> None:
        spec = minimal_spec([{
            "id": "m1", "from": "a", "to": "b",
            "label": "创建远程沙箱", "method": "Sandbox.create(scope)",
        }])
        spec["title"] = "请求级沙箱生命周期"
        svg, _, _ = renderer.render(spec)
        self.assertIn("创建远程沙箱", svg)
        self.assertIn("Sandbox.create(scope)", svg)
        self.assertIn('fill="#62676E"', svg)
        root = ET.fromstring(svg)
        arrow = next(element for element in root.iter() if element.get("data-role") == "step-arrow")
        texts = {
            "".join(element.itertext()): element
            for element in root.iter()
            if element.tag.endswith("text")
        }
        arrow_y = float(arrow.get("y1", "0"))
        self.assertLess(float(texts["1. 创建远程沙箱"].get("y", "0")), arrow_y)
        self.assertGreater(float(texts["Sandbox.create(scope)"].get("y", "0")), arrow_y)

    def test_legacy_technical_field_remains_compatible(self) -> None:
        spec = minimal_spec([{
            "id": "m1", "from": "a", "to": "b",
            "label": "创建远程沙箱", "technical": "Sandbox.create(scope)",
        }])
        spec["title"] = "请求级沙箱生命周期"
        svg, _, _ = renderer.render(spec)
        self.assertIn("Sandbox.create(scope)", svg)

    def test_method_rejects_explanatory_text(self) -> None:
        spec = minimal_spec([{
            "id": "m1", "from": "a", "to": "b",
            "label": "创建远程沙箱", "method": "create sandbox and return handle",
        }])
        spec["title"] = "请求级沙箱生命周期"
        with self.assertRaisesRegex(renderer.SpecError, "core method name"):
            renderer.validate(spec)

    def test_every_message_is_rendered_as_a_directional_step_arrow(self) -> None:
        events = [
            {"id": "m1", "from": "a", "to": "b", "label": "Call"},
            {"id": "m2", "from": "b", "to": "a", "label": "Return", "kind": "return"},
            {"id": "m3", "from": "a", "to": "a", "label": "Internal", "kind": "self"},
        ]
        svg, _, _ = renderer.render(minimal_spec(events))
        root = ET.fromstring(svg)
        arrows = [element for element in root.iter() if element.get("data-role") == "step-arrow"]
        arrowheads = [element for element in root.iter() if element.get("data-role") == "step-arrowhead"]
        self.assertEqual(len(arrows), len(events))
        self.assertEqual(len(arrowheads), len(events))
        self.assertEqual(
            [(arrow.get("data-from"), arrow.get("data-to")) for arrow in arrows],
            [("a", "b"), ("b", "a"), ("a", "a")],
        )
        self.assertEqual(
            [(head.get("data-from"), head.get("data-to")) for head in arrowheads],
            [("a", "b"), ("b", "a"), ("a", "a")],
        )
        self.assertTrue(all(head.tag.endswith("polygon") for head in arrowheads))
        self.assertTrue(all(head.get("points") for head in arrowheads))

    def test_long_activation_is_rejected(self) -> None:
        events = [
            {"id": f"m{index}", "from": "a", "to": "b", "label": f"Step {index}"}
            for index in range(1, 9)
        ]
        spec = minimal_spec(events)
        spec["activations"] = [{"participant": "a", "from": "m1", "to": "m8"}]
        with self.assertRaisesRegex(renderer.SpecError, "activation.*too long"):
            renderer.validate(spec)

    def test_short_activation_is_still_supported(self) -> None:
        events = [
            {"id": "m1", "from": "a", "to": "b", "label": "Start"},
            {"id": "m2", "from": "b", "to": "a", "label": "Done", "kind": "return"},
        ]
        spec = minimal_spec(events)
        spec["activations"] = [{"participant": "b", "from": "m1", "to": "m2"}]
        svg, _, _ = renderer.render(spec)
        self.assertIn('width="10"', svg)

    def test_unnumbered_protocol_signal_may_remain_technical_only(self) -> None:
        spec = minimal_spec([{"from": "a", "to": "b", "label": "ACK", "number": False}])
        spec["title"] = "消息确认流程"
        renderer.validate(spec)

    def test_bundled_example_renders_valid_svg(self) -> None:
        with (SKILL_DIR / "assets" / "example-sequence.json").open(encoding="utf-8") as handle:
            spec = json.load(handle)
        svg, width, height = renderer.render(spec)
        ET.fromstring(svg)
        self.assertGreater(width, 1000)
        self.assertGreater(height, 1000)
        self.assertIn("alt · 库存检查结果", svg)
        self.assertIn("loop · 资料补充", svg)

    def test_nested_fragments_and_parallel_operands(self) -> None:
        fragment = {
            "kind": "fragment",
            "operator": "par",
            "branches": [
                {"condition": "worker A", "events": [{"from": "a", "to": "b", "label": "A job"}]},
                {
                    "condition": "worker B",
                    "events": [{
                        "kind": "fragment", "operator": "opt",
                        "branches": [{"condition": "enabled", "events": [{"from": "b", "to": "a", "label": "B job"}]}]
                    }],
                },
            ],
        }
        svg, _, _ = renderer.render(minimal_spec([fragment]))
        self.assertIn(">par<", svg)
        self.assertIn(">opt<", svg)
        self.assertIn("[worker A]", svg)

    def test_all_message_kinds_use_clear_filled_directional_arrowheads(self) -> None:
        events = [
            {"from": "a", "to": "b", "label": "Call", "color": "#008000"},
            {"from": "b", "to": "a", "label": "Return", "kind": "return"},
            {"from": "a", "to": "b", "label": "Signal", "kind": "async"},
            {"from": "a", "to": "a", "label": "Internal", "kind": "self"},
            {"from": "a", "to": "b", "label": "Caution", "emphasis": "warning"},
        ]
        svg, _, _ = renderer.render(minimal_spec(events))
        root = ET.fromstring(svg)
        arrows = [element for element in root.iter() if element.get("data-role") == "step-arrow"]
        arrowheads = [element for element in root.iter() if element.get("data-role") == "step-arrowhead"]
        self.assertEqual(svg.count('stroke-dasharray="6 4"'), 1)
        self.assertEqual(len(arrows), len(events))
        self.assertEqual(len(arrowheads), len(events))
        self.assertEqual([head.get("fill") for head in arrowheads], ["#008000", "#202326", "#202326", "#202326", "#B87510"])
        self.assertTrue(all("marker-end" not in arrow.attrib for arrow in arrows))

    def test_arrowhead_is_compact_and_touches_receiver_lifeline(self) -> None:
        svg, _, _ = renderer.render(minimal_spec())
        root = ET.fromstring(svg)
        head = next(element for element in root.iter() if element.get("data-role") == "step-arrowhead")
        target_lifeline = next(
            element for element in root.iter()
            if element.get("data-role") == "lifeline" and element.get("data-participant") == "b"
        )
        points = [tuple(map(float, point.split(","))) for point in head.get("points", "").split()]
        tip_x = points[0][0]
        base_x = points[1][0]
        height = abs(points[2][1] - points[1][1])
        self.assertAlmostEqual(tip_x, float(target_lifeline.get("x1", "nan")))
        self.assertLessEqual(abs(tip_x - base_x), 10)
        self.assertLessEqual(height, 10)

    def test_extra_canvas_width_expands_between_participants_not_left_margin(self) -> None:
        spec = {
            "title": "Wide flow",
            "participants": [{"id": f"p{index}", "label": f"P{index}"} for index in range(6)],
            "phases": [{"label": "Phase", "events": [{"from": "p0", "to": "p5", "label": "Call"}]}],
            "layout": {"preset": "presentation", "participant_spacing": 170, "participant_width": 156, "min_width": 1750},
        }
        svg, width, _ = renderer.render(spec)
        root = ET.fromstring(svg)
        lifelines = [element for element in root.iter() if element.get("data-role") == "lifeline"]
        self.assertEqual(len(lifelines), 6)
        first_x = float(lifelines[0].get("x1", "nan"))
        last_x = float(lifelines[-1].get("x1", "nan"))
        self.assertLessEqual(first_x, 230)
        self.assertGreaterEqual(last_x, width - 230)

    def test_long_unbroken_identifier_wraps(self) -> None:
        lines = renderer.wrap_text("THIS_IS_A_SINGLE_UNBROKEN_IDENTIFIER", 10)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(renderer.display_width(line) <= 10 for line in lines))

    def test_presentation_preset_is_larger(self) -> None:
        _, web_width, _ = renderer.render(minimal_spec(), "web")
        _, presentation_width, _ = renderer.render(minimal_spec(), "presentation")
        self.assertGreater(presentation_width, web_width)

    def test_english_legend_title_is_localized(self) -> None:
        spec = minimal_spec()
        spec["legend"] = [{"term": "OK", "text": "Successful"}]
        svg, _, _ = renderer.render(spec)
        self.assertIn(">Legend<", svg)

    def test_strict_validation_errors_are_spec_errors(self) -> None:
        invalid_specs = []
        bad_layout = minimal_spec()
        bad_layout["layout"] = "wide"
        invalid_specs.append(bad_layout)
        bad_legend = minimal_spec()
        bad_legend["legend"] = ["not an object"]
        invalid_specs.append(bad_legend)
        bad_self = minimal_spec([{"from": "a", "to": "b", "label": "Wrong", "kind": "self"}])
        invalid_specs.append(bad_self)
        bad_fragment = minimal_spec([{"kind": "fragment", "operator": "alt", "branches": [{"events": [{"from": "a", "to": "b", "label": "Only"}]}]}])
        invalid_specs.append(bad_fragment)
        for spec in invalid_specs:
            with self.subTest(spec=spec):
                with self.assertRaises(renderer.SpecError):
                    renderer.validate(spec)

    def test_svg_round_trip_to_file(self) -> None:
        svg, _, _ = renderer.render(minimal_spec())
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagram.svg"
            path.write_text(svg, encoding="utf-8")
            ET.parse(path)


if __name__ == "__main__":
    unittest.main()
