import * as opentype from 'opentype.js';
import type { Font, Path as FontPath } from 'opentype.js';
import paper from 'paper';
import { generateComposition, describeBounds } from './legacy/composition-geometry.js';
import { primitiveKit as p, contourPath } from './legacy/boundary-geometry.js';
import type { DesignInput, DesignVariant, VariantOption } from './types';

export const WORDMARK_VARIANTS: VariantOption[] = [
  { id: 'weave', name: '桥接连字' }, { id: 'fusion', name: '共笔衔接' },
  { id: 'counter', name: '负形刻口' }, { id: 'axis', name: '轴线断点' },
  { id: 'embrace', name: '内缘圆切' }, { id: 'tension', name: '斜切端点' },
];
const fonts = new Map<number, Font>();
const parseFont: typeof opentype.parse = opentype.parse ?? Reflect.get(opentype, 'default').parse;
let loading: Promise<void> | undefined;
const scope = new paper.PaperScope();
scope.setup(new scope.Size(1, 1));
scope.settings.insertItems = false;
const FONT_SIZE = 120, BASELINE = 140;
const round = (value: number) => Math.round(value * 10000) / 10000;
const rect = (box: paper.Rectangle): [number, number, number, number] =>
  [round(box.x), round(box.y), round(box.width), round(box.height)];
const inkArea = (item: paper.PathItem) => Math.abs((item as paper.Path | paper.CompoundPath).area);
function fontPathData(path: FontPath): string {
  // opentype 2.0's numeric serializer can turn 414.00000000000006 into NaN.
  // Serialize its original M/L/Q/C commands directly, retaining every curve.
  return path.commands.map(command => {
    if (command.type === 'M' || command.type === 'L') return `${command.type} ${round(command.x)} ${round(command.y)}`;
    if (command.type === 'Q') return `Q ${round(command.x1)} ${round(command.y1)} ${round(command.x)} ${round(command.y)}`;
    if (command.type === 'C') return `C ${round(command.x1)} ${round(command.y1)} ${round(command.x2)} ${round(command.y2)} ${round(command.x)} ${round(command.y)}`;
    return 'Z';
  }).join(' ');
}

export async function loadWordmarkFonts(baseUrl: string): Promise<void> {
  if (fonts.size === 5) return;
  if (!loading) {
    loading = Promise.all([300, 400, 500, 600, 700].map(async weight => {
      const response = await fetch(`${baseUrl.replace(/\/$/, '')}/lean-design-${weight}.ttf`);
      if (!response.ok) throw new Error('字库加载失败，请重试');
      const font = parseFont(await response.arrayBuffer());
      if (![...'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'].every(letter => font.charToGlyphIndex(letter) > 0)) {
        throw new Error('字库不完整，请重新加载');
      }
      return [weight, font] as const;
    })).then(entries => { for (const [weight, font] of entries) fonts.set(weight, font); })
      .catch(cause => { loading = undefined; throw new Error('字库加载失败，请重试', { cause }); });
  }
  await loading;
}

interface GlyphItem { character: string; index: number; item: paper.PathItem }
interface Accent { indices: number[]; detail: string; connector?: paper.PathItem }

function glyphItems(input: DesignInput): { items: GlyphItem[]; weight: number; capY: number } {
  const weight = Math.max(300, Math.min(700, 300 + Math.round((input.weight - 14) / 6) * 100));
  const font = fonts.get(weight);
  if (!font) throw new Error('字库尚未加载，请重试');
  const tracking = FONT_SIZE * (.018 + input.spacing * .00075);
  const gap = Math.max(.9, tracking * .45);
  let cursor = 0, previous: ReturnType<Font['charToGlyph']> | undefined;
  const items: GlyphItem[] = [];
  for (const [index, character] of [...input.text].entries()) {
    const glyph = font.charToGlyph(character);
    if (previous) cursor += font.getKerningValue(previous, glyph) * FONT_SIZE / font.unitsPerEm;
    const item = new scope.CompoundPath({ pathData: fontPathData(glyph.getPath(cursor, BASELINE, FONT_SIZE)), insert: false });
    const right = items.at(-1)?.item.bounds.right;
    // Preserve optical advances/kerning, but never merge an entire name into a blob.
    if (right !== undefined && item.bounds.left < right + gap) {
      const correction = right + gap - item.bounds.left;
      item.translate(new scope.Point(correction, 0)); cursor += correction;
    }
    items.push({ character, index, item });
    cursor += (glyph.advanceWidth ?? font.unitsPerEm * .6) * FONT_SIZE / font.unitsPerEm + tracking;
    previous = glyph;
  }
  return { items, weight, capY: font.charToGlyph('H').getPath(0, BASELINE, FONT_SIZE).getBoundingBox().y1 };
}

function seededPosition(seed: number, length: number) {
  return ((seed % length) + length) % length;
}
function choosePair(items: GlyphItem[], seed: number, focusIndex?: number) {
  if (focusIndex !== undefined) return Math.min(focusIndex, items.length - 2);
  const candidates = items.slice(0, -1).map((entry, index) => ({
    index, score: Math.min(entry.item.bounds.width, items[index + 1]!.item.bounds.width),
  })).sort((a, b) => b.score - a.score || a.index - b.index);
  return candidates[seededPosition(seed, Math.min(3, candidates.length))]?.index ?? 0;
}
function chooseGlyph(items: GlyphItem[], seed: number, focusIndex?: number) {
  if (focusIndex !== undefined) return items[focusIndex]!;
  const candidates = [...items].sort((a, b) => inkArea(b.item) - inkArea(a.item) || a.index - b.index);
  return candidates[seededPosition(seed, Math.min(3, candidates.length))]!;
}
function inkAt(item: paper.PathItem, y: number, side: 'left' | 'right') {
  const box = item.bounds;
  for (let step = 1; step < 120; step++) {
    const x = side === 'right' ? box.right - box.width * step / 120 : box.left + box.width * step / 120;
    if (item.contains(new scope.Point(x, y))) return x;
  }
  return undefined;
}

function connectPair(items: GlyphItem[], input: DesignInput, shared: boolean): Accent | undefined {
  if (items.length < 2) return;
  const index = choosePair(items, input.seed, input.focusIndex);
  const first = items[index]!, second = items[index + 1]!;
  const top = Math.max(first.item.bounds.top, second.item.bounds.top);
  const bottom = Math.min(first.item.bounds.bottom, second.item.bounds.bottom, BASELINE);
  const density = (input.overlap - 24) / 44;
  const ratios = shared ? [.84, .74, .64, .54] : [.32, .46, .59, .7];
  for (const ratio of ratios) {
    const y = top + (bottom - top) * ratio;
    const left = inkAt(first.item, y, 'right'), right = inkAt(second.item, y, 'left');
    if (left === undefined || right === undefined || right - left > FONT_SIZE * .36) continue;
    const thickness = (shared ? 3.4 : 1.5) + density * (shared ? 3 : 1.2) + input.tension * .008;
    const bridge = new scope.Path.Rectangle({ rectangle: new scope.Rectangle(left - 1, y - thickness / 2, right - left + 2, thickness), insert: false });
    const joined = first.item.unite(bridge, { insert: false }).unite(second.item, { insert: false });
    bridge.remove();
    // A single local bridge involves one pair; the rest of the word stays untouched.
    return { indices: [index, index + 1], connector: joined,
      detail: `${index + 1}–${index + 2} 位 ${first.character}${second.character}，${shared ? '共用一段横笔' : '一处细桥连接'}` };
  }
}

function sampleInk(item: paper.PathItem) {
  const box = item.bounds, points: paper.Point[] = [];
  // Distributed real ink samples avoid creating details in an empty counter.
  for (let row = 2; row <= 14; row++) for (let column = 2; column <= 16; column++) {
    const point = new scope.Point(box.x + box.width * column / 18, box.y + box.height * row / 16);
    if (item.contains(point)) points.push(point);
  }
  return points;
}
function cutAccent(items: GlyphItem[], input: DesignInput, kind: string): Accent {
  const entry = chooseGlyph(items, input.seed, input.focusIndex), original = entry.item, box = original.bounds;
  const energy = input.tension / 100, density = (input.overlap - 24) / 44;
  const points = sampleInk(original);
  points.sort((a, b) => {
    if (kind === 'tension') return (b.x - b.y * .35) - (a.x - a.y * .35);
    if (kind === 'embrace') return Math.abs(a.x - box.center.x) - Math.abs(b.x - box.center.x);
    return Math.abs(a.y - (box.y + box.height * .47)) - Math.abs(b.y - (box.y + box.height * .47));
  });
  const area = inkArea(original);
  const base = Math.max(1.1, Math.min(5.2, (input.weight / 38) * 4 + density + energy * .7));
  for (const point of points.slice(0, 48)) {
    let cutter: paper.PathItem;
    if (kind === 'counter') {
      // One small actual counter; no white overlay, filter or raster mask.
      cutter = new scope.Path.Rectangle({ rectangle: new scope.Rectangle(point.x - base * .6, point.y - base * .45, base * 1.2, base * .9), insert: false });
    } else if (kind === 'axis') {
      cutter = new scope.Path.Rectangle({ rectangle: new scope.Rectangle(point.x - base * 1.8, point.y - base * .23, base * 3.6, base * .46), insert: false });
    } else if (kind === 'embrace') {
      cutter = new scope.Path.Circle({ center: point, radius: base * .68, insert: false });
    } else {
      cutter = new scope.Path({ segments: [[point.x - base, point.y - base * .8], [point.x + base * 2.6, point.y - base * .8], [point.x + base * 2.6, point.y + base * .9]], closed: true, insert: false });
    }
    const changed = original.subtract(cutter, { insert: false });
    cutter.remove();
    const retained = inkArea(changed) / area;
    if (retained >= .9 && retained <= .997 && changed.bounds.width >= box.width * .95 && changed.bounds.height >= box.height * .95) {
      original.remove(); entry.item = changed;
      const effect = { counter: '一处负形刻口', axis: '一个横向断点', embrace: '一处圆形留白', tension: '一处斜切细节' }[kind] ?? '一处局部细节';
      return { indices: [entry.index], detail: `第 ${entry.index + 1} 位 ${entry.character}，${effect}` };
    }
    changed.remove();
  }
  return { indices: [], detail: '保留完整字形与光学间距' };
}

export function signatureLetters(input: Pick<DesignInput, 'text' | 'markText'>): NonNullable<DesignVariant['signature']> {
  if (input.markText) return { markText: input.markText, source: 'custom' };
  const boundary = [...input.text].findIndex((letter, index) => index > 0 && /[a-z]/.test(input.text[index - 1]!) && /[A-Z]/.test(letter));
  if (boundary > 0) return { markText: input.text[0]! + input.text[boundary]!, source: 'camelcase' };
  return { markText: input.text.slice(0, 2), source: 'initials' };
}

function signatureMark(input: DesignInput, id: string, nameBounds: paper.Rectangle) {
  const signature = signatureLetters(input);
  const geometry = generateComposition({ letters: signature.markText, weight: input.weight, spacing: 0,
    overlap: input.overlap, tension: input.tension, seed: input.seed, strategy: id });
  const [x, y, width, height] = describeBounds(geometry.contours);
  const scale = Math.min(130 / width, 112 / height);
  const targetX = 0, targetY = nameBounds.center.y - height * scale / 2;
  const transform = { scale, x: targetX - x * scale, y: targetY - y * scale };
  const fitted = p.transform(p.region(geometry.contours), transform);
  let cursor = 0;
  const contours = geometry.contours.map((loop: unknown[]) => {
    const result = fitted.edges.slice(cursor, cursor + loop.length); cursor += loop.length; return result;
  });
  return { signature, width: width * scale, paths: contours.map(contourPath),
    arcs: contours.flat().filter((edge: { circle?: number[] }) => !!edge.circle).length,
    segments: contours.flat().length,
    bounds: [0, targetY, width * scale, height * scale] as [number, number, number, number],
    circles: geometry.circles.map(([cx, cy, r]: number[]) => [cx! * scale + transform.x, cy! * scale + transform.y, r! * scale]),
    lines: geometry.lines.map((edge: { from: number[]; to: number[] }) => [edge.from[0]! * scale + transform.x, edge.from[1]! * scale + transform.y, edge.to[0]! * scale + transform.x, edge.to[1]! * scale + transform.y]),
    joints: geometry.joints.map((joint: { point: number[]; tangent: number[] }) => ({ point: [joint.point[0]! * scale + transform.x, joint.point[1]! * scale + transform.y], tangent: joint.tangent })),
  };
}

export function generateWordmark(input: DesignInput, option: VariantOption, signatureLayout = false): DesignVariant {
  scope.activate();
  const { items, weight, capY } = glyphItems(input);
  // Local edits must not shift the other letters or the independent signature mark.
  const nameBounds = items.map(entry => entry.item.bounds).reduce((a, b) => a.unite(b));
  let accent: Accent | undefined;
  if (option.id === 'weave' || option.id === 'fusion') accent = connectPair(items, input, option.id === 'fusion');
  accent ??= cutAccent(items, input, option.id === 'weave' ? 'axis' : option.id === 'fusion' ? 'counter' : option.id);
  const mark = signatureLayout ? signatureMark(input, option.id, nameBounds) : undefined;
  const nameOffset = mark ? mark.width + 35 - nameBounds.x : -nameBounds.x;
  for (const entry of items) entry.item.translate(new scope.Point(nameOffset, 0));
  accent.connector?.translate(new scope.Point(nameOffset, 0));
  const placements = items.map(entry => ({ character: entry.character, index: entry.index, path: entry.item.pathData, bounds: rect(entry.item.bounds) }));
  const renderItems = accent.connector
    ? [...items.filter(entry => !accent!.indices.includes(entry.index)).map(entry => entry.item), accent.connector]
    : items.map(entry => entry.item);
  const paths = [...(mark?.paths.length ? [mark.paths.join(' ')] : []), ...renderItems.map(item => item.pathData)];
  const renderedBounds = renderItems.map(item => item.bounds).reduce((a, b) => a.unite(b));
  const bounds = mark ? renderedBounds.unite(new scope.Rectangle(...mark.bounds)) : renderedBounds;
  const paddingX = 30, paddingY = 40;
  const lines = [[nameBounds.x + nameOffset - 8, BASELINE, nameBounds.right + nameOffset + 8, BASELINE],
    [nameBounds.x + nameOffset - 8, capY, nameBounds.right + nameOffset + 8, capY],
    ...(mark?.lines ?? [])];
  const contours = renderItems.reduce((sum, item) => sum + (item instanceof scope.CompoundPath ? item.children.length : 1), mark?.paths.length ?? 0);
  const segments = renderItems.reduce((sum, item) => sum + (item instanceof scope.CompoundPath ? (item.children as paper.Path[]).reduce((total, path) => total + path.segments.length, 0) : (item as paper.Path).segments.length), 0);
  const result: DesignVariant = {
    id: option.id, name: option.name, text: input.text, mode: 'geometry', paths, fillRule: 'evenodd',
    viewBox: [round(bounds.x - paddingX), round(bounds.y - paddingY), round(bounds.width + paddingX * 2), round(bounds.height + paddingY * 2)],
    bounds: rect(bounds), guides: { paths, lines, circles: mark?.circles ?? [], joints: mark?.joints ?? [] },
    placements, resolvedWeight: weight, layout: signatureLayout ? 'signature' : 'wordmark',
    description: signatureLayout ? '独立图形与完整名称' : '完整字标 · 局部定制',
    detail: accent.detail, modifiedIndices: accent.indices, signature: mark?.signature,
    statistics: { characters: input.text.length, groups: signatureLayout ? 2 : 1, contours, arcs: mark?.arcs ?? 0, segments: segments + (mark?.segments ?? 0) },
  };
  for (const entry of items) entry.item.remove();
  accent.connector?.remove();
  if (result.paths.some(path => !path || /NaN|Infinity/.test(path)) || !result.bounds.every(Number.isFinite)) throw new Error('字标轮廓生成失败，请调整参数');
  return result;
}
