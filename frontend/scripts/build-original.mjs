import { copyFile, mkdir, realpath, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const source = path.join(frontend, 'original');
const creator = await realpath(path.resolve(frontend, '../web/creator'));
const output = path.join(creator, 'design');
// Only replace this generated directory; never follow a redirected output path.
try {
  if (await realpath(output) !== output) throw new Error('Unexpected design output path');
} catch (error) { if (error.code !== 'ENOENT') throw error; }
if (path.dirname(output) !== creator || path.basename(output) !== 'design') throw new Error('Unsafe output path');
await rm(output, { recursive: true, force: true });
const files = ['index.html', 'design-brand.css', 'logo-geometry.js', 'logo-mark.svg', 'logo-favicon.svg',
  'monogram-mvp/styles.css', 'monogram-mvp/generator.js', 'monogram-mvp/boundary-geometry.js'];
for (const file of files) {
  const target = path.join(output, file);
  await mkdir(path.dirname(target), { recursive: true });
  await copyFile(path.join(source, file), target);
}
console.log(`Original monogram page published locally: ${files.length} files.`);
