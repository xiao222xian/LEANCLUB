import type { DesignVariant } from '../engine/types';

/** Crop the real final paths around the changed letters, never redraw a substitute. */
export function focusContentBox(variant: DesignVariant): { x: number; y: number; width: number; height: number } {
  const items = variant.placements.filter(p => variant.modifiedIndices?.includes(p.index));
  if (!items.length) return { x: variant.viewBox[0], y: variant.viewBox[1], width: variant.viewBox[2], height: variant.viewBox[3] };
  const left = Math.min(...items.map(p => p.bounds[0]));
  const top = Math.min(...items.map(p => p.bounds[1]));
  const right = Math.max(...items.map(p => p.bounds[0] + p.bounds[2]));
  const bottom = Math.max(...items.map(p => p.bounds[1] + p.bounds[3]));
  return { x: left, y: top - .01, width: right - left, height: bottom - top + .02 };
}
export function focusViewBox(variant: DesignVariant): [number, number, number, number] {
  if (!variant.modifiedIndices?.length) return variant.viewBox;
  const box = focusContentBox(variant);
  const margin = Math.max(8, box.height * .17);
  return [box.x - margin, box.y - margin, box.width + margin * 2, box.height + margin * 2];
}
