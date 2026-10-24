import { generateVariants } from '../src/engine/index.ts';
import type { DesignInput } from '../src/engine/types.ts';
const input: DesignInput = { text: 'SR', mode: 'geometry', weight: 24, spacing: 0, overlap: 44, tension: 52, seed: 0 };
for (const text of ['SR', 'Bj', 'LEAN', 'LeanSc', 'LeanSchool', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef', 'iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii']) {
  const results = [];
  for (let i = 0; i < 6; i++) {
    const start = performance.now();
    const variants = generateVariants({ ...input, text, seed: i });
    results.push(performance.now() - start);
    if (variants.length !== 6) throw new Error('incomplete candidate set');
  }
  const sorted = results.slice(1).sort((a, b) => a - b);
  console.log(JSON.stringify({ text, firstMs: +results[0].toFixed(2), warmMedianMs: +sorted[Math.floor(sorted.length / 2)].toFixed(2), warmMaxMs: +sorted.at(-1)!.toFixed(2) }));
}
