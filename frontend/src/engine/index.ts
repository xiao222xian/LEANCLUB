import { generateComposition, describePlacement, describeBounds } from './legacy/composition-geometry.js';
import type { DesignInput, DesignVariant, VariantOption } from './types';
import { generateWordmark, loadWordmarkFonts, WORDMARK_VARIANTS } from './wordmark';

export type { DesignInput, DesignVariant, DesignPlacement, DesignMode, DesignComposition } from './types';
export const MAX_TEXT_LENGTH = 32;
export const GEOMETRY_VARIANTS: VariantOption[] = [
  { id: 'weave', name: '交错穿插' }, { id: 'fusion', name: '轮廓熔合' },
  { id: 'counter', name: '负形嵌入' }, { id: 'axis', name: '共轴压合' },
  { id: 'embrace', name: '内外抱合' }, { id: 'tension', name: '斜向咬合' },
];
/** Monograms remain network-free. The Worker explicitly loads fonts for wordmarks. */
export async function initializeEngine(fontBaseUrl?: string): Promise<void> {
  if (fontBaseUrl) await loadWordmarkFonts(fontBaseUrl);
}

export function resolveComposition(input: Pick<DesignInput, 'text' | 'composition'>): 'monogram' | 'wordmark' | 'signature' {
  if (!input.composition || input.composition === 'auto') return input.text.length <= 2 ? 'monogram' : 'wordmark';
  return input.composition;
}

export function validateInput(input: DesignInput): void {
  if (!input.text) throw new Error('输入英文字母');
  if (!/^[A-Za-z]+$/.test(input.text)) throw new Error('目前支持 A–Z、a–z，大小写保留');
  if (input.text.length > MAX_TEXT_LENGTH) throw new Error(`最多输入 ${MAX_TEXT_LENGTH} 个英文字母`);
  for (const [name, value, min, max] of [
    ['字重', input.weight, 14, 38], ['间距', input.spacing, -20, 80],
    ['重叠', input.overlap, 24, 68], ['张力', input.tension, 0, 100],
  ] as const) {
    if (!Number.isFinite(value) || value < min || value > max) throw new Error(`${name}超出可用范围`);
  }
  if (!Number.isSafeInteger(input.seed)) throw new Error('无效的方案编号');
  if (input.mode !== 'geometry') throw new Error('无效的设计模式');
  if (input.composition && !['auto', 'monogram', 'wordmark', 'signature'].includes(input.composition)) throw new Error('无效的构图模式');
  if (input.focusIndex !== undefined && (!Number.isInteger(input.focusIndex) || input.focusIndex < 0 || input.focusIndex >= input.text.length)) throw new Error('局部设计请选择名称中的一个字母');
  if (resolveComposition(input) === 'signature' && input.markText !== undefined && input.markText !== '' && !/^[A-Za-z]{1,4}$/.test(input.markText)) throw new Error('图形字母支持 1–4 个英文字母');
}
export function getVariantOptions(_mode: DesignInput['mode'] = 'geometry', composition: DesignInput['composition'] = 'monogram', text = ''): VariantOption[] {
  return resolveComposition({ composition, text }) === 'monogram' ? GEOMETRY_VARIANTS : WORDMARK_VARIANTS;
}
export function generateVariant(input: DesignInput, id: string): DesignVariant {
  validateInput(input);
  const layout = resolveComposition(input);
  const option = (layout === 'monogram' ? GEOMETRY_VARIANTS : WORDMARK_VARIANTS).find(candidate => candidate.id === id);
  if (!option) throw new Error('无效的设计方案');
  if (layout !== 'monogram') return generateWordmark(input, option, layout === 'signature');
  let geometry: ReturnType<typeof generateComposition>;
  try {
    geometry = generateComposition({
      letters: input.text, weight: input.weight, overlap: input.overlap,
      tension: input.tension, spacing: input.spacing * 0.4, seed: input.seed, strategy: id,
    });
  } catch (cause) {
    throw new Error(`“${option.name}”暂时无法完成轮廓组合，请调整重叠或字重后重试`, { cause });
  }
  if (!geometry.paths.length || geometry.paths.some((path: string) => !path || /NaN|Infinity/.test(path))) {
    throw new Error('该组合暂时无法生成，请调整重叠后重试');
  }
  const placements = geometry.placementShapes.map((shape: unknown, index: number) => ({
    character: input.text[index], index, ...describePlacement(shape),
  }));
  return {
    id: option.id, name: option.name, text: input.text, mode: 'geometry',
    paths: [geometry.paths.join(' ')], fillRule: 'evenodd', viewBox: [0, 0, 320, 200],
    bounds: describeBounds(geometry.contours) as [number, number, number, number],
    guides: {
      paths: geometry.paths, circles: geometry.circles,
      lines: geometry.lines.map((edge: { from: number[]; to: number[] }) => [...edge.from, ...edge.to]),
      joints: geometry.joints,
    },
    placements, resolvedWeight: input.weight, layout: 'monogram',
    statistics: {
      characters: input.text.length, groups: geometry.groupCount,
      contours: geometry.contours.length, arcs: geometry.contours.flat().filter((edge: { circle?: number[] }) => !!edge.circle).length,
      segments: geometry.contours.flat().length,
    },
  };
}
export function generateVariants(input: DesignInput): DesignVariant[] {
  validateInput(input);
  return getVariantOptions(input.mode, input.composition ?? 'auto', input.text).map(option => generateVariant(input, option.id));
}
