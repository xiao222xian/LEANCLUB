import test from 'node:test';
import assert from 'node:assert/strict';
import { generateComposition, describeBounds, describePlacement } from '../src/engine/legacy/composition-geometry.js';

const strategies = ['weave', 'fusion', 'counter', 'axis', 'embrace', 'tension'];
const screenshot = { letters: 'SRRR', weight: 24, overlap: 66, tension: 57, spacing: 32 * 0.4, seed: 0 };

function verify(geometry: ReturnType<typeof generateComposition>) {
  assert(geometry.paths.length > 0);
  assert(!/NaN|Infinity/.test(geometry.paths.join('')));
  assert.equal(geometry.placementShapes.length, 4, 'all four screenshot letters contribute');
  const [x, y, width, height] = describeBounds(geometry.contours);
  assert(x >= 0 && y >= 0 && width > 0 && height > 0 && x + width <= 320.01 && y + height <= 200.01);
  for (const loop of geometry.contours) loop.forEach((edge, index) => {
    const next = loop[(index + 1) % loop.length];
    assert(Math.hypot(edge.to[0] - next.from[0], edge.to[1] - next.from[1]) < 1e-5);
    if (edge.circle) for (const point of [edge.from, edge.to]) {
      assert(Math.abs(Math.hypot(point[0] - edge.circle[0], point[1] - edge.circle[1]) - edge.circle[2]) < 1e-5);
    }
  });
  // The actual Worker posts the derived result, not analytic `contains`
  // functions. Verify that every transferable geometry field remains cloneable.
  const transfer = {
    paths: geometry.paths, circles: geometry.circles, lines: geometry.lines,
    joints: geometry.joints, bounds: describeBounds(geometry.contours),
    placements: geometry.placementShapes.map(describePlacement),
  };
  assert.deepEqual(structuredClone(transfer), transfer);
}

test('screenshot regression: SRRR / weight24 overlap66 tension57 spacing32, all six strategies', () => {
  for (const strategy of strategies) verify(generateComposition({ ...screenshot, strategy }));
});

test('screenshot repeated-letter geometry remains valid across deterministic seeds', () => {
  for (const seed of [1, 2, 3, 4, 7, 12, 32, 57, 99, 1000, 1000000]) {
    for (const strategy of strategies) verify(generateComposition({ ...screenshot, seed, strategy }));
  }
});

test('screenshot neighbourhood and parameter boundaries produce real closed paths', () => {
  for (const [weight, overlap, tension, spacing] of [
    [14, 24, 0, -20], [14, 68, 100, 80], [38, 24, 100, -20], [38, 68, 0, 80],
    [24, 65, 56, 31], [24, 67, 58, 33], [24, 68, 57, 32],
  ]) for (const strategy of strategies) {
    verify(generateComposition({ ...screenshot, weight, overlap, tension, spacing: spacing * 0.4, strategy }));
  }
});
