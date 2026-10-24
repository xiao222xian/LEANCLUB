import { generateComposition, describeBounds } from '../src/engine/legacy/composition-geometry.js';
const failures: unknown[] = [];
let count = 0;
const start = performance.now();
for (const weight of [14, 24, 38]) for (const overlap of [24, 44, 66, 68]) for (const tension of [0, 52, 57, 100]) for (const spacing of [-20, 0, 32, 80]) for (const seed of [0, 1]) {
  for (const strategy of ['weave', 'fusion', 'counter', 'axis', 'embrace', 'tension']) {
    const input = { letters: 'SRRR', weight, overlap, tension, spacing: spacing * 0.4, seed, strategy };
    count++;
    try {
      const result = generateComposition(input);
      if (!result.paths.length || /NaN|Infinity/.test(result.paths.join(''))) throw new Error('invalid paths');
      const b = describeBounds(result.contours);
      if (!b.every(Number.isFinite)) throw new Error('invalid bounds');
      for (const loop of result.contours) loop.forEach((edge, i) => {
        const next = loop[(i + 1) % loop.length];
        if (Math.hypot(edge.to[0] - next.from[0], edge.to[1] - next.from[1]) >= 1e-5) throw new Error('open contour');
      });
    } catch (error) {
      failures.push({ ...input, error: error instanceof Error ? error.message : String(error) });
    }
  }
}
console.log(JSON.stringify({ configurations: count, failures: failures.length, examples: failures.slice(0, 16), durationMs: +(performance.now() - start).toFixed(2) }));
if (failures.length) process.exitCode = 1;
