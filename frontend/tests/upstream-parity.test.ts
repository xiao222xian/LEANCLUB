import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { generateVariant } from '../src/engine/index.ts';

type Joint = { point: number[]; tangent: number[] };
type HashField = 'paths' | 'circles' | 'lines' | 'joints';
type Profile = { weight: number; overlap: number; tension: number; seed: number };
type ParityCase = {
  text: string;
  profile: string;
  strategy: string;
  counts: Record<HashField, number>;
  sha256: Record<HashField, string>;
};
type Fixture = {
  schemaVersion: number;
  upstream: { repository: string; commit: string; sourceSha256: Record<string, string> };
  profiles: Record<string, Profile>;
  cases: ParityCase[];
};

// Independent expected values were generated once from upstream 677e8f8.
// This test reads only the committed fixture and the local production adapter.
// Do not regenerate expectations from src/engine/legacy: that would hide drift.
const fixture = JSON.parse(readFileSync(new URL('./fixtures/upstream-parity.json', import.meta.url), 'utf8')) as Fixture;
const hash = (value: unknown) => createHash('sha256').update(JSON.stringify(value), 'utf8').digest('hex');
const texts = ['SR', 'RS', 'BJ', 'OG', 'AA', 'NX'];
const strategies = ['weave', 'fusion', 'counter', 'axis', 'embrace', 'tension'];

test('upstream parity fixture pins the independent original implementation and representative matrix', () => {
  assert.equal(fixture.schemaVersion, 1);
  assert.equal(fixture.upstream.repository, 'https://github.com/auenger/SiliRootLogoDesign');
  assert.equal(fixture.upstream.commit, '677e8f8f1a3d7128d8ab831cae99d7e00ac567ce');
  assert.equal(fixture.cases.length, texts.length * strategies.length * Object.keys(fixture.profiles).length);
  assert.equal(Object.keys(fixture.profiles).length, 4);
  assert.equal(new Set(fixture.cases.map(item => `${item.text}/${item.profile}/${item.strategy}`)).size, fixture.cases.length);
  for (const text of texts) for (const profile of Object.keys(fixture.profiles)) {
    assert.deepEqual(fixture.cases.filter(item => item.text === text && item.profile === profile).map(item => item.strategy), strategies);
  }
});

for (const text of texts) {
  test(`upstream exact parity: ${text}, all six relationships and four parameter profiles`, () => {
    for (const sample of fixture.cases.filter(item => item.text === text)) {
      const input = { text, ...fixture.profiles[sample.profile], spacing: 0, mode: 'geometry' as const };
      const context = `${text}/${sample.strategy}/${sample.profile} ${JSON.stringify(input)}`;
      const variant = generateVariant(input, sample.strategy);
      const guides = variant.guides as typeof variant.guides & { joints: Joint[] };
      assert.equal(variant.text, text, context);
      assert.equal(variant.mode, 'geometry', context);
      assert.equal(variant.fillRule, 'evenodd', context);
      assert.equal(variant.resolvedWeight, input.weight, `${context}: preserve original stroke-weight units`);
      assert.deepEqual(variant.viewBox, [0, 0, 320, 200], `${context}: preserve original canvas`);
      assert.ok(Array.isArray(guides.joints), `${context}: retain original tangent joints`);

      const actual: Record<HashField, unknown> = {
        paths: variant.paths.join(' '),
        circles: guides.circles,
        lines: guides.lines,
        joints: guides.joints.map(joint => ({ point: joint.point, tangent: joint.tangent })),
      };
      for (const field of ['paths', 'circles', 'lines', 'joints'] as const) {
        assert.equal(hash(actual[field]), sample.sha256[field], `${context}: ${field} differs from upstream`);
      }
      assert.equal(hash(guides.paths.join(' ')), sample.sha256.paths, `${context}: guide contours must match exported contours`);
      assert.deepEqual({
        paths: guides.paths.length,
        circles: guides.circles.length,
        lines: guides.lines.length,
        joints: guides.joints.length,
      }, sample.counts, `${context}: preserve contour and construction counts`);
    }
  });
}
