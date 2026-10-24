import { generateVariant } from '../src/engine/index.ts';
import type { DesignInput } from '../src/engine/types.ts';
const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
const strategies = ['weave', 'fusion', 'counter', 'axis', 'embrace', 'tension'];
const base: DesignInput = { text: '', mode: 'geometry', weight: 24, spacing: 0, overlap: 44, tension: 52, seed: 0 };
const failures: { text: string; strategy: string; error: string }[] = [];
let count = 0;
const started = performance.now();
for (const first of letters) for (const second of letters) {
  const text = first + second;
  if (/^[A-Z]{2}$/.test(text)) continue;
  for (const strategy of strategies) {
    count++;
    try {
      const variant = generateVariant({ ...base, text }, strategy);
      if (variant.placements.length !== 2 || /NaN|Infinity/.test(variant.paths.join(''))) throw new Error('invalid geometry');
    } catch (error) {
      failures.push({ text, strategy, error: error instanceof Error ? error.message : String(error) });
    }
  }
}
console.log(JSON.stringify({ configurations: count, failureCount: failures.length, examples: failures.slice(0, 24), durationMs: +(performance.now() - started).toFixed(2) }));
if (failures.length) process.exitCode = 1;
