import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const source = new URL('../original/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('upstream.json', source), 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
for (const file of ['monogram-mvp/styles.css', 'monogram-mvp/boundary-geometry.js', 'logo-geometry.js', 'design-brand.css', 'logo-mark.svg', 'logo-favicon.svg']) {
  assert.equal(hash(await readFile(new URL(file, source))), manifest.files[file], `${file} must remain byte-identical to upstream`);
}
const generator = (await readFile(new URL('monogram-mvp/generator.js', source), 'utf8')).replaceAll('\r\n', '\n').replace('LEAN SCHOOL / DESIGN PROCESS', 'SILIROOT / DESIGN PROCESS');
assert.equal(hash(generator), manifest.generatorNormalizedSha256, 'Generator may only change its brand label');
const original = await readFile(new URL('monogram-mvp/index.html', source), 'utf8');
const current = await readFile(new URL('index.html', source), 'utf8');
const workspace = html => html.slice(html.indexOf('    <header class="topbar">'), html.indexOf('    <footer')).replace('LEAN SCHOOL / MONOGRAM STUDY', 'SILIROOT / MONOGRAM STUDY');
assert.equal(workspace(current), workspace(original).replaceAll('\r\n', '\n'), 'The original workspace layout and controls must remain unchanged');
console.log('Upstream CSS, geometry, generator behavior and workspace markup verified.');
