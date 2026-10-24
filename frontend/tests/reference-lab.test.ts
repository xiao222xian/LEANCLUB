import { strict as assert } from 'node:assert';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { capPolygon, measureReferenceTangency, parseReferenceGeometry, phaseContains,
  polygonClip, referenceCircles } from '../src/lib/reference-lab';

const asset = (file: string) => new URL(`../public/reference/${file}`, import.meta.url);
const source = JSON.parse(readFileSync(asset('sr-geometry.json'), 'utf8'));
const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

test('fixed SR resources retain the original image and all three geometry parts', () => {
  assert.equal(createHash('sha256').update(readFileSync(asset('sr-original.png'))).digest('hex'),
    '252d4a7c1069fecc47d578017c7d9775fc84840a9719edd3d65d2b27667dc9ad');
  assert.equal(source.source.commit, '677e8f8f1a3d7128d8ab831cae99d7e00ac567ce');
  const geometry = parseReferenceGeometry(source), circles = referenceCircles(geometry);
  assert.deepEqual(geometry.parts.map(part => part.name), ['S 上段', 'R', 'S 下段']);
  assert.equal(geometry.parts.flatMap(part => part.arcs).length, 46);
  assert.equal(circles.length, 36);
  assert.equal(circles.filter(circle => phaseContains('base', circle)).length, 0);
  assert.equal(circles.filter(circle => phaseContains('add', circle)).length, 18);
  assert.equal(circles.filter(circle => phaseContains('final', circle)).length, 36);
  assert.ok(circles.filter(circle => !phaseContains('add', circle)).every(circle => !circle.add));
});

test('Vue study preserves upstream polygon bases and all circular-cap clips exactly', () => {
  const geometry = parseReferenceGeometry(source);
  // Expected hashes were produced independently from the pinned upstream
  // logo-lab.html formulas and logo-geometry.js. No local upstream path at runtime.
  assert.equal(hash(geometry.parts.map(part => polygonClip([part.start, ...part.arcs.map(arc => arc.to)], geometry))),
    'cd87a6ea0d3847e0c4c0b085615977125cd8e1a6317167eb74e830dc3dac94f7');
  assert.equal(hash(referenceCircles(geometry).map(circle => polygonClip(capPolygon(circle, geometry), geometry))),
    'd10b4b23892bf9aca87a4fd4c038dd8b47271ad27025fe5470118ce18c561467');
});

test('tangency is measured from edges and reports a damaged designated join', () => {
  const geometry = parseReferenceGeometry(source), original = measureReferenceTangency(geometry);
  assert.equal(original.expected, 33);
  assert.equal(original.complete, true);
  assert.ok(original.maxError !== null && original.maxError < 1e-10);
  const circle = geometry.parts.flatMap(part => part.arcs).find(arc => arc.circle)!;
  circle.circle![0] += 20;
  const damaged = measureReferenceTangency(geometry);
  assert.equal(damaged.joints.length, 33, 'a bad tangent must not be filtered out');
  assert.ok(damaged.maxError !== null && damaged.maxError > .1);
  geometry.smoothJointPoints.push([10000, 10000]);
  assert.equal(measureReferenceTangency(geometry).complete, false);
});

test('invalid reference data fails explicitly instead of drawing an incomplete SR', () => {
  for (const mutation of [
    (value: typeof source) => { value.parts.pop(); },
    (value: typeof source) => { value.parts[0].arcs[0].from[0] += 1; },
    (value: typeof source) => { value.parts[0].arcs.find((arc: { circle?: number[] }) => arc.circle).circle[2] = -1; },
    (value: typeof source) => { value.smoothJointPoints[0] = [null, 5]; },
  ]) {
    const value = structuredClone(source); mutation(value);
    assert.throws(() => parseReferenceGeometry(value));
  }
});
