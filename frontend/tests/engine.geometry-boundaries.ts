import assert from 'node:assert/strict';
import { generateVariants } from '../src/engine/index.ts';

let count = 0;
const start = performance.now();
for (const text of ['LS', 'SR', 'BJ', 'OO']) {
  for (const weight of [14, 24, 38]) for (const overlap of [24, 68]) for (const spacing of [-20, 80]) {
    const variants = generateVariants({ text, weight, overlap, spacing, tension: 52, seed: 1, mode: 'geometry' });
    for (const variant of variants) {
      assert(variant.paths.length > 0 && !/NaN|Infinity/.test(variant.paths.join('')));
      const [x, y, width, height] = variant.bounds;
      assert(x >= 0 && y >= 0 && width > 0 && height > 0 && x + width <= 320.01 && y + height <= 200.01);
      count++;
    }
  }
}
console.log(JSON.stringify({ configurations: count, durationMs: +(performance.now() - start).toFixed(2) }));
