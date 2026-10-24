import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import paper from 'paper';
import { initializeEngine, generateVariant, generateVariants, resolveComposition, getVariantOptions, validateInput } from '../src/engine/index.ts';
import { signatureLetters } from '../src/engine/wordmark.ts';
import { generateBoundary } from '../src/engine/legacy/boundary-geometry.js';
import type { DesignInput, DesignVariant } from '../src/engine/types.ts';

const defaults: DesignInput = { text: 'LeanSchool', weight: 24, spacing: 0, overlap: 44, tension: 52, seed: 0, mode: 'geometry', composition: 'auto' };
const scope = new paper.PaperScope();
scope.setup(new scope.Size(1, 1));
scope.settings.insertItems = false;
before(async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async () => new Response('', { status: 503 });
  await assert.rejects(initializeEngine('/unavailable-fonts'), /字库加载失败/);
  let requests = 0;
  globalThis.fetch = async url => {
    requests++;
    const bytes = await fs.readFile(fileURLToPath(new URL(`../public/fonts/${String(url).split('/').at(-1)}`, import.meta.url)));
    return new Response(bytes, { status: 200 });
  };
  try {
    await Promise.all([initializeEngine('/fixture-fonts'), initializeEngine('/fixture-fonts')]);
    assert.equal(requests, 5, 'failed loading can retry, and concurrent retry shares one five-font request');
  }
  finally { globalThis.fetch = saved; }
});

function assertComplete(variant: DesignVariant, text: string) {
  assert.equal(variant.text, text);
  assert.equal(variant.placements.map(placement => placement.character).join(''), text);
  assert.equal(variant.placements.length, text.length);
  assert.equal(variant.statistics.characters, text.length);
  assert(variant.paths.length > 0 && variant.paths.every(path => path && !/NaN|Infinity/.test(path)));
  assert((variant.modifiedIndices?.length ?? 0) <= 2);
  const [x, y, width, height] = variant.viewBox;
  const [left, top, bw, bh] = variant.bounds;
  assert(left >= x && top >= y && left + bw <= x + width + .001 && top + bh <= y + height + .001);
  for (let index = 0; index < variant.placements.length; index++) {
    const current = variant.placements[index]!;
    assert(current.path.length > 0 && current.bounds.every(Number.isFinite));
    if (index > 0) {
      const previous = variant.placements[index - 1]!;
      assert(current.bounds[0] > previous.bounds[0] + previous.bounds[2], 'full-name glyphs remain separately readable');
    }
  }
}

test('auto keeps short monograms and routes complete longer names to wordmarks', () => {
  assert.equal(resolveComposition({ text: 'L' }), 'monogram');
  assert.equal(resolveComposition({ text: 'LS', composition: 'auto' }), 'monogram');
  assert.equal(resolveComposition({ text: 'LeanSchool', composition: 'auto' }), 'wordmark');
  assert.equal(resolveComposition({ text: 'LeanSchool', composition: 'monogram' }), 'monogram');
  assert.equal(getVariantOptions('geometry', 'wordmark')[0]!.name, '桥接连字');
  assert.equal(getVariantOptions('geometry')[0]!.name, '交错穿插');
});

test('original two-uppercase geometry remains byte-for-byte equivalent in auto', () => {
  for (const text of ['SR', 'LS', 'BJ']) for (const option of getVariantOptions()) {
    const variant = generateVariant({ ...defaults, text }, option.id);
    const legacy = generateBoundary({ letters: text, weight: 24, spacing: 0, overlap: 44, tension: 52, seed: 0, strategy: option.id });
    assert.equal(variant.paths[0], legacy.paths.join(' '));
    assert.equal(variant.layout, 'monogram');
  }
});

test('wordmarks preserve every character and case in one stable horizontal reading order', () => {
  for (const text of ['L', 'i', 'Bj', 'LEAN', 'Lean', 'LeanSchool', 'jiggy', 'iiii', 'B'.repeat(16), 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef']) {
    let variants: DesignVariant[];
    try { variants = generateVariants({ ...defaults, text, composition: 'wordmark' }); }
    catch (cause) { throw new Error(`wordmark ${text}`, { cause }); }
    for (const variant of variants) {
      assertComplete(variant, text);
      assert.equal(variant.layout, 'wordmark');
      assert(variant.guides.circles.length === 0, 'font Bezier curves are not mislabelled as construction circles');
    }
  }
  const cases = ['LEAN', 'Lean', 'lean'].map(text => generateVariant({ ...defaults, text }, 'weave').paths.join(''));
  assert.equal(new Set(cases).size, 3);
});

test('six directions make actual distinct local changes without cutting the whole name', () => {
  const variants = generateVariants(defaults);
  assert.equal(new Set(variants.map(variant => variant.paths.join(''))).size, 6);
  for (const variant of variants) {
    assert((variant.modifiedIndices?.length ?? 0) >= 1);
    assert((variant.modifiedIndices?.length ?? 0) <= 2);
    assert(variant.detail && variant.description);
  }
  for (const variant of generateVariants({ ...defaults, text: 'LeanSchoolDesignStudio' })) {
    assertComplete(variant, 'LeanSchoolDesignStudio');
    assert((variant.modifiedIndices?.length ?? 0) <= 2);
    assert(variant.viewBox[2] > variant.viewBox[3] * 4, 'long names expand the horizontal canvas instead of stacking');
  }
});

test('local negative details retain at least ninety percent of original glyph ink', () => {
  const baseline = generateVariant(defaults, 'weave');
  for (const id of ['counter', 'axis', 'embrace', 'tension']) {
    const variant = generateVariant(defaults, id);
    scope.activate();
    for (const index of variant.modifiedIndices ?? []) {
      const original = new scope.CompoundPath({ pathData: baseline.placements[index]!.path, insert: false });
      const changed = new scope.CompoundPath({ pathData: variant.placements[index]!.path, insert: false });
      const retained = Math.abs(changed.area / original.area);
      assert(retained >= .9 - .0001 && retained <= .9971, `${id}: restrained actual subtraction`);
      assert(!original.subtract(changed, { insert: false }).isEmpty(), 'removed ink is real geometry');
      original.remove(); changed.remove();
    }
  }
});

test('signature letters explain camel-case boundaries, ordinary initials and custom symbols', () => {
  assert.deepEqual(signatureLetters({ text: 'LeanSchool' }), { markText: 'LS', source: 'camelcase' });
  assert.deepEqual(signatureLetters({ text: 'leanschool' }), { markText: 'le', source: 'initials' });
  assert.deepEqual(signatureLetters({ text: 'LEAN' }), { markText: 'LE', source: 'initials' });
  assert.deepEqual(signatureLetters({ text: 'LeanSchool', markText: 'Bj' }), { markText: 'Bj', source: 'custom' });
  for (const text of ['L', 'LeanSchool', 'leanschool']) {
    const variant = generateVariant({ ...defaults, text, composition: 'signature' }, 'fusion');
    assertComplete(variant, text);
    assert.equal(variant.layout, 'signature');
    assert.equal(variant.statistics.groups, 2);
    assert(variant.signature?.markText);
    assert(variant.placements[0]!.bounds[0] > variant.bounds[0] + 35, 'independent symbol precedes full name');
  }
});

test('real font weight, tracking and seed vary construction while preserving reproducibility', () => {
  const first = generateVariant(defaults, 'counter');
  assert.deepEqual(first, generateVariant(defaults, 'counter'));
  assert.notDeepEqual(first.paths, generateVariant({ ...defaults, weight: 38 }, 'counter').paths);
  assert.notDeepEqual(first.paths, generateVariant({ ...defaults, seed: 1 }, 'counter').paths);
  const tight = generateVariant({ ...defaults, spacing: -20 }, 'fusion');
  const loose = generateVariant({ ...defaults, spacing: 80 }, 'fusion');
  assert(loose.bounds[2] > tight.bounds[2]);
});

test('short and long font extremes stay finite, and invalid custom symbols fail explicitly', () => {
  for (const weight of [14, 38]) for (const text of ['jiggy', 'W'.repeat(32), 'i'.repeat(32)]) {
    let variants: DesignVariant[];
    try { variants = generateVariants({ ...defaults, text, weight, overlap: weight === 14 ? 24 : 68, tension: weight === 14 ? 0 : 100 }); }
    catch (cause) { throw new Error(`wordmark extreme ${text}/${weight}`, { cause }); }
    for (const variant of variants) {
      assertComplete(variant, text);
    }
  }
  for (const markText of ['ABCDE', '薄肌', 'A1']) assert.throws(() => generateVariant({ ...defaults, composition: 'signature', markText }, 'fusion'));
});

test('focused negative details edit only the selected real glyph and retain all other paths', () => {
  const untouched = generateVariant(defaults, 'weave').placements;
  for (const focusIndex of [0, 3, 9]) for (const id of ['counter', 'axis', 'embrace', 'tension']) {
    const variant = generateVariant({ ...defaults, focusIndex }, id);
    assert.deepEqual(variant.modifiedIndices, [focusIndex], `${id} targets position ${focusIndex}`);
    assert.notEqual(variant.placements[focusIndex]!.path, untouched[focusIndex]!.path);
    for (const placement of variant.placements) if (placement.index !== focusIndex) {
      assert.deepEqual(placement, untouched[placement.index], 'unselected letters retain their exact outlines and position');
    }
    assert.deepEqual(variant.paths, generateVariant({ ...defaults, focusIndex, seed: 17 }, id).paths, 'manual focus overrides automatic seed selection');
  }
});

test('focused bridges use the next character, with the last character selecting the preceding pair', () => {
  for (const id of ['weave', 'fusion']) for (const focusIndex of [0, 1, 3]) {
    const variant = generateVariant({ ...defaults, text: 'AAAA', focusIndex }, id);
    const first = Math.min(focusIndex, 2);
    assert.deepEqual(variant.modifiedIndices, [first, first + 1]);
    assert.equal(variant.paths.length, 3, 'the selected pair is united as real geometry');
    assertComplete(variant, 'AAAA');
  }
  const single = generateVariant({ ...defaults, text: 'A', composition: 'wordmark', focusIndex: 0 }, 'weave');
  assert.deepEqual(single.modifiedIndices, [0], 'one-letter wordmark falls back to a local detail');
});

test('signature focus affects the full name while the independent symbol remains identical', () => {
  for (const option of getVariantOptions('geometry', 'wordmark')) {
    const start = generateVariant({ ...defaults, composition: 'signature', focusIndex: 0 }, option.id);
    const end = generateVariant({ ...defaults, composition: 'signature', focusIndex: 9 }, option.id);
    assert.deepEqual(start.signature, { markText: 'LS', source: 'camelcase' });
    assert.equal(start.paths[0], end.paths[0], 'the exact symbol geometry and placement ignore full-name focus');
    assert((end.modifiedIndices ?? []).includes(9));
    assert((end.modifiedIndices ?? []).every(index => index === 8 || index === 9));
    assertComplete(end, 'LeanSchool');
  }
});

test('focus is optional, strictly bounded, and does not change legacy monograms', () => {
  for (const focusIndex of [-1, 10, .5, Number.NaN, Number.POSITIVE_INFINITY, '0', null]) {
    assert.throws(() => validateInput({ ...defaults, focusIndex } as DesignInput), /局部设计/);
  }
  assert.doesNotThrow(() => validateInput({ ...defaults, focusIndex: 0 }));
  assert.doesNotThrow(() => validateInput({ ...defaults, focusIndex: 9 }));
  assert.deepEqual(generateVariant({ ...defaults, focusIndex: undefined }, 'counter'), generateVariant(defaults, 'counter'));
  for (const option of getVariantOptions()) {
    const monogram = { ...defaults, text: 'LS' };
    assert.deepEqual(generateVariant({ ...monogram, focusIndex: 1 }, option.id), generateVariant(monogram, option.id));
  }
});
