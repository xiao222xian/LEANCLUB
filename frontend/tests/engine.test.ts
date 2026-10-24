import test from 'node:test';
import assert from 'node:assert/strict';
import { initializeEngine, generateVariants, generateVariant, MAX_TEXT_LENGTH } from '../src/engine/index.ts';
import type { DesignInput } from '../src/engine/types.ts';
import { generateBoundary, primitiveKit as p } from '../src/engine/legacy/boundary-geometry.js';
import { generateComposition } from '../src/engine/legacy/composition-geometry.js';

const defaults: DesignInput = { text: 'Lean', weight: 24, spacing: 0, overlap: 44, tension: 52, seed: 0, mode: 'geometry', composition: 'monogram' };
const strategies = ['weave', 'fusion', 'counter', 'axis', 'embrace', 'tension'];

function assertExactContours(geometry: ReturnType<typeof generateComposition>) {
  assert(geometry.contours.length > 0);
  for (const loop of geometry.contours) loop.forEach((edge, index) => {
    const next = loop[(index + 1) % loop.length];
    assert(Math.hypot(edge.to[0] - next.from[0], edge.to[1] - next.from[1]) < 1e-5, 'closed analytic edges');
    if (edge.circle) for (const point of [edge.from, edge.to]) {
      assert(Math.abs(Math.hypot(point[0] - edge.circle[0], point[1] - edge.circle[1]) - edge.circle[2]) < 1e-5, 'actual circular arc');
    }
  });
}

function assertValid(input: DesignInput) {
  const variants = generateVariants(input);
  assert.equal(variants.length, 6);
  for (const variant of variants) {
    assert.equal(variant.text, input.text);
    assert.equal(variant.mode, 'geometry');
    assert.equal(variant.placements.map(item => item.character).join(''), input.text);
    assert.equal(variant.placements.length, input.text.length);
    assert.equal(variant.statistics.characters, input.text.length);
    assert(!/NaN|Infinity/.test(variant.paths.join('')));
    assert.equal(variant.fillRule, 'evenodd');
    assert.equal(variant.paths.length, 1, 'all contours share the correct evenodd compound');
    const [x, y, width, height] = variant.bounds;
    assert(x >= -0.01 && y >= -0.01 && width > 0 && height > 0 && x + width <= 320.01 && y + height <= 200.01, `${input.text}/${variant.id}: final contours in frame`);
    for (const placement of variant.placements) {
      assert(placement.path && placement.bounds.every(Number.isFinite));
      assert(placement.bounds[2] > 0 && placement.bounds[3] > 0);
    }
  }
  return variants;
}

test('all 52 analytic glyphs resolve at all original stroke extremes', () => {
  for (const weight of [14, 24, 38]) for (const character of 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz') {
    const shape = p.resolvedGlyph(character, weight);
    assert(shape.edges.length > 0, `${character}/${weight}`);
    assert(shape.edges.every(edge => [...edge.from, ...edge.to, ...(edge.circle || [])].every(Number.isFinite)));
  }
});

test('single letters, mixed case and longer names retain every input character', () => {
  for (const text of ['B', 'i', 'j', 'g', 'Bj', 'bj', 'BJ', 'LEAN', 'Lean', 'LEANS', 'LeanSc', 'LeanSchool', 'iiii', 'BBBB', 'jiggy', 'pqpq', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef']) {
    assertValid({ ...defaults, text });
  }
  const signatures = ['BJ', 'Bj', 'bj'].map(text => generateVariant({ ...defaults, text }, 'fusion').paths.join(''));
  assert.equal(new Set(signatures).size, 3, 'case has genuinely different geometry');
});

test('six relationship strategies have distinct silhouettes and reproducible seeds', () => {
  for (const text of ['B', 'Bj', 'LEAN', 'LeanSchool']) {
    const variants = assertValid({ ...defaults, text });
    assert.equal(new Set(variants.map(item => item.paths.join(''))).size, 6, `${text}: six different geometric results`);
  }
  const original = generateVariants(defaults);
  assert.deepEqual(original, generateVariants(defaults));
  assert.notDeepEqual(original, generateVariants({ ...defaults, seed: 3 }));
});

test('lowercase construction retains dots, x-height and descenders', () => {
  for (const character of ['i', 'j']) {
    const shape = p.resolvedGlyph(character, 24);
    assert(shape.contains([character === 'i' ? 39.6 : 38.4, 24]), `${character}: separate round dot`);
    assert(!shape.contains([character === 'i' ? 39.6 : 38.4, 42]), `${character}: transparent dot/stem separation`);
  }
  const aBounds = p.resolvedGlyph('a', 24).edges.map(p.edgeBounds);
  assert(Math.min(...aBounds.map(box => box[1])) >= 48);
  for (const character of ['g', 'j', 'p', 'q', 'y']) {
    const boxes = p.resolvedGlyph(character, 24).edges.map(p.edgeBounds);
    assert(Math.max(...boxes.map(box => box[3])) > 170, `${character}: actual descender`);
  }
});

test('counterforms are actual empty regions and new paths keep exact circular arcs', () => {
  for (const character of ['O', 'o']) {
    const geometry = generateComposition({ letters: character, strategy: 'fusion', weight: 24, overlap: 44, tension: 52, spacing: 0, seed: 0 });
    assert.equal(geometry.contains([160, 98]), false, 'transparent O counter');
    assert(geometry.contours.length >= 2);
    assertExactContours(geometry);
  }
  for (const letters of ['Bj', 'Lean', 'jiggy', 'LeanSchool']) for (const strategy of strategies) {
    assertExactContours(generateComposition({ letters, strategy, weight: 24, overlap: 44, tension: 52, spacing: 0, seed: 0 }));
  }
});

test('new lowercase/multiple-letter parameter extremes remain finite and preserve all input', () => {
  for (const weight of [14, 38]) for (const overlap of [24, 68]) for (const tension of [0, 100]) {
    for (const text of ['Bj', 'ijgp', 'LeanSc']) {
      assertValid({ ...defaults, text, weight, overlap, tension, spacing: overlap === 24 ? -20 : 80 });
    }
  }
  for (const text of ['W'.repeat(MAX_TEXT_LENGTH), 'i'.repeat(MAX_TEXT_LENGTH)]) {
    assertValid({ ...defaults, text });
  }
});

test('reject unsupported input and parameter ranges explicitly', () => {
  for (const text of ['', '你好', 'Lean School', 'Logo1', 'A'.repeat(MAX_TEXT_LENGTH + 1)]) {
    assert.throws(() => generateVariants({ ...defaults, text }));
  }
  for (const weight of [0, 39, NaN, Infinity]) assert.throws(() => generateVariants({ ...defaults, weight }));
  assert.throws(() => generateVariants({ ...defaults, tension: -1 }));
  assert.throws(() => generateVariants({ ...defaults, mode: 'type' } as unknown as DesignInput));
});

test('original 234 configurations retain closed edges and exact circular arcs', () => {
  let count = 0;
  for (const letters of ['SR', 'AI', 'OG', 'NX', 'TL', 'BQ', 'CD', 'EF', 'HJ', 'KM', 'PU', 'VW', 'YZ']) {
    for (const weight of [14, 24, 38]) for (const strategy of strategies) {
      const geometry = generateBoundary({ letters, weight, strategy, overlap: weight === 14 ? 68 : 24, tension: weight === 38 ? 100 : 0 });
      assertExactContours(geometry);
      count++;
    }
  }
  assert.equal(count, 234);
});

test('spacing changes all six original relationships and both endpoints stay in bounds', () => {
  for (const text of ['LS', 'SR', 'BJ', 'OO']) {
    const tight = assertValid({ ...defaults, text, spacing: -20 });
    const loose = assertValid({ ...defaults, text, spacing: 80 });
    tight.forEach((variant, index) => assert.notDeepEqual(variant.paths, loose[index].paths, `${text}/${variant.id}`));
  }
});

test('analytic engine initializes without fetching fonts or requiring a canvas', async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('No network is permitted'); };
  try {
    await initializeEngine();
    assertValid({ ...defaults, text: 'Bj' });
  } finally { globalThis.fetch = saved; }
});
