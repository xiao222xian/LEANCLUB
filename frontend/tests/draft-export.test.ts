import { strict as assert } from "node:assert";
import { test } from "node:test";
import { DEFAULT_INPUT, parseDraft } from "../src/lib/draft";
import { svgDocument } from "../src/lib/export";
import type { DesignVariant } from "../src/engine/types";

const draft = {
  version: 3,
  input: { ...DEFAULT_INPUT, text: "LeanSchool", mode: "geometry" },
  selectedId: "weave",
  ink: "#163a2c",
  background: "#a8e8c0",
  guides: false,
};
test("draft restoration preserves case and rejects corrupted or coerced values", () => {
  assert.equal(parseDraft(JSON.stringify(draft))?.input.text, "LeanSchool");
  for (const text of [
    null,
    undefined,
    {},
    [],
    42,
    "",
    "A".repeat(33),
    "hello world",
  ])
    assert.equal(
      parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, text } })),
      null,
    );
  for (const ink of [null, undefined, {}, ["#163a2c"], "red"])
    assert.equal(parseDraft(JSON.stringify({ ...draft, ink })), null);
  for (const seed of [0.5, -1, Infinity, 1000001, "2"])
    assert.equal(
      parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, seed } })),
      null,
    );
  assert.equal(parseDraft("null"), null);
  assert.equal(parseDraft("{bad json"), null);
  assert.equal(
    parseDraft(
      JSON.stringify({ ...draft, input: { ...draft.input, mode: "type" } }),
    ),
    null,
  );
  const restored = parseDraft(
    JSON.stringify({
      ...draft,
      untrusted: "discard",
      input: { ...draft.input, untrusted: "discard" },
    }),
  )!;
  assert.equal("untrusted" in restored, false);
  assert.equal("untrusted" in restored.input, false);
  for (const tension of [null, undefined, "52", -1, 101]) {
    assert.equal(
      parseDraft(
        JSON.stringify({ ...draft, input: { ...draft.input, tension } }),
      ),
      null,
    );
  }
  assert.equal(parseDraft(JSON.stringify({ ...draft, version: 1 })), null);
  assert.equal(parseDraft(JSON.stringify({ ...draft, version: 2 })), null);
  const signature = parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, composition: 'signature', markText: 'LS', focusIndex: 4 } }));
  assert.equal(signature?.input.composition, 'signature');
  assert.equal(signature?.input.markText, 'LS');
  assert.equal(signature?.input.focusIndex, 4);
  for (const focusIndex of [-1, 0.5, '4', null, 10]) assert.equal(parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, focusIndex } })), null);
  for (const composition of ['unknown', 0, [], null, '']) assert.equal(parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, composition } })), null);
  for (const markText of ['ABCDE', 'LS!', 0, [], null]) assert.equal(parseDraft(JSON.stringify({ ...draft, input: { ...draft.input, markText } })), null);
  assert.equal(
    parseDraft(
      JSON.stringify({ ...draft, input: { ...draft.input, weight: 600 } }),
    ),
    null,
  );
});

const fixture: DesignVariant = {
  id: "fusion",
  name: "轮廓熔合",
  text: "Oo",
  mode: "geometry",
  paths: ["M0 0L100 0L100 100L0 100ZM20 20L20 80L80 80L80 20Z"],
  fillRule: "nonzero",
  viewBox: [0, 0, 320, 200],
  bounds: [0, 0, 100, 100],
  placements: [],
  guides: { paths: [], lines: [], circles: [], joints: [] },
  resolvedWeight: 24,
  statistics: { characters: 2, groups: 1, contours: 2, arcs: 0, segments: 8 },
};
test("SVG export retains real contours, input case, fill rule, dimensions and transparency", () => {
  const svg = svgDocument(fixture, "#163a2c", undefined, 2048);
  assert.ok(svg.includes("<title>Oo · 轮廓熔合</title>"));
  assert.ok(svg.includes('width="2048" height="1280"'));
  assert.ok(svg.includes(`d="${fixture.paths[0]}"`));
  assert.ok(svg.includes('fill-rule="nonzero"'));
  assert.ok(!svg.includes("<rect"));
  assert.ok(!svg.includes("<text"));
  assert.ok(!svg.includes("font-family"));
  assert.ok(
    svgDocument(
      { ...fixture, fillRule: "evenodd" },
      "#163a2c",
      "#a8e8c0",
    ).includes('fill-rule="evenodd"'),
  );
  assert.ok(
    svgDocument(fixture, "#163a2c", "#a8e8c0").includes('fill="#a8e8c0"'),
  );
  assert.throws(() => svgDocument(fixture, "url(https://bad.invalid)"));
  assert.throws(() =>
    svgDocument({ ...fixture, paths: ["M NaN 0"] }, "#163a2c"),
  );
});
