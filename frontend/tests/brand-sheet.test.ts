import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { generateVariant } from '../src/engine';
import { BRAND_PHOTO_URL, brandSheetDocument, loadBrandPhoto } from '../src/lib/brand-sheet';
import { svgDocument } from '../src/lib/export';

const variant = generateVariant({ text: 'SR', weight: 24, overlap: 44, spacing: 0, tension: 52, seed: 0, mode: 'geometry' }, 'weave');
const photo = 'data:image/webp;base64,UklGRg==';
const colors = { color: '#21372c', background: '#b2edc4' };
const pathData = (xml: string) => [...xml.matchAll(/<path d="([^"]+)"\/>/g)].map(match => match[1]);

test('both application SVGs retain the original logo paths, viewBox and hole rule without changing the variant', () => {
  const original = structuredClone(variant);
  for (const scene of ['card', 'poster'] as const) {
    const sheet = brandSheetDocument(variant, { scene, ...colors, photoDataUrl: photo });
    assert.deepEqual(pathData(sheet.svg), pathData(svgDocument(variant, colors.color)));
    assert.ok(sheet.svg.includes(`viewBox="${variant.viewBox.join(' ')}"`));
    assert.ok(sheet.svg.includes(`fill-rule="${variant.fillRule}"`));
    assert.ok(sheet.svg.includes('应用样张'));
    assert.ok(sheet.svg.includes('多运动。'));
    assert.ok(sheet.svg.includes('多思考。'));
    assert.equal(sheet.svg.includes('NaN'), false);
    assert.equal(/<script|<foreignObject|<link|@import/i.test(sheet.svg), false);
    assert.equal(/href="(?:https?:|\/)/.test(sheet.svg), false);
    assert.deepEqual([sheet.width, sheet.height], scene === 'card' ? [1200, 700] : [900, 1200]);
    assert.equal((sheet.svg.match(/<image /g) ?? []).length, scene === 'poster' ? 1 : 0);
  }
  assert.deepEqual(variant, original);
});

test('poster embeds the exact existing local photo bytes, with no external image dependency', async () => {
  const source = await readFile(new URL('../../web/creator/images/hero-1024-v2.webp', import.meta.url));
  const requests: string[] = [];
  const photoDataUrl = await loadBrandPhoto(async url => {
    requests.push(String(url));
    return new Response(source, { headers: { 'Content-Type': 'image/webp' } });
  });
  assert.deepEqual(requests, [BRAND_PHOTO_URL]);
  assert.deepEqual(Buffer.from(photoDataUrl.split(',')[1]!, 'base64'), source);
  const sheet = brandSheetDocument(variant, { scene: 'poster', ...colors, photoDataUrl });
  assert.ok(sheet.svg.includes(`href="${photoDataUrl}"`));
  assert.equal(sheet.svg.includes(BRAND_PHOTO_URL), false);
});

test('application export rejects unloaded or linked photos, markup injection, failed fetch and HTML fallbacks', async () => {
  for (const photoDataUrl of [undefined, '', '/images/hero-1024-v2.webp', 'https://example.test/photo.webp', 'data:image/svg+xml;base64,UklGRg==']) {
    assert.throws(() => brandSheetDocument(variant, { scene: 'poster', ...colors, photoDataUrl }), /照片尚未加载/);
  }
  assert.throws(() => brandSheetDocument(variant, { scene: 'card', ...colors, color: '"/><script/>' }), /颜色/);
  assert.throws(() => brandSheetDocument({ ...variant, paths: ['<script>'] }, { scene: 'card', ...colors }), /轮廓/);
  await assert.rejects(loadBrandPhoto(async () => new Response('missing', { status: 404 })), /照片加载失败/);
  await assert.rejects(loadBrandPhoto(async () => new Response('<html>', { headers: { 'Content-Type': 'text/html' } })), /照片格式无效/);
  await assert.rejects(loadBrandPhoto(async () => new Response('', { headers: { 'Content-Type': 'image/webp' } })), /照片为空/);
});
