/** Fixed SR study, adapted from SiliRootLogoDesign/public/logo-lab.html.
 * Source: 677e8f8f1a3d7128d8ab831cae99d7e00ac567ce. See public/reference/SOURCES.md.
 * Geometry uses actual circular caps and straight polygons, including the
 * upstream one-source-pixel overlap at each chord to avoid antialias seams.
 */
export type Point = [number, number];
export type ReferencePhase = 'base' | 'add' | 'final';
export type ReferenceMode = 'plain' | 'overlay' | 'diff';
export interface ReferenceArc {
  from: Point;
  to: Point;
  circle?: [number, number, number];
  add?: boolean;
}
export interface ReferencePart { name: string; start: Point; arcs: ReferenceArc[] }
export interface ReferenceGeometry {
  width: number;
  height: number;
  parts: ReferencePart[];
  smoothJointPoints: Point[];
}
export interface ReferenceCircle extends ReferenceArc {
  circle: [number, number, number];
  add: boolean;
  id: number;
  name: string;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const point = (value: unknown): value is Point => Array.isArray(value) && value.length === 2 && value.every(finite);
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);

export function parseReferenceGeometry(value: unknown): ReferenceGeometry {
  if (!record(value) || value.width !== 520 || value.height !== 505 || !Array.isArray(value.parts)
    || value.parts.length !== 3 || !Array.isArray(value.smoothJointPoints) || !value.smoothJointPoints.every(point)) {
    throw new Error('SR 原型几何资源格式无效');
  }
  const parts: ReferencePart[] = value.parts.map(part => {
    if (!record(part) || typeof part.name !== 'string' || !point(part.start) || !Array.isArray(part.arcs) || !part.arcs.length) {
      throw new Error('SR 原型轮廓格式无效');
    }
    let previous = part.start;
    const arcs: ReferenceArc[] = part.arcs.map(arc => {
      if (!record(arc) || !point(arc.from) || !point(arc.to) || distance(previous, arc.from) > 1e-7 || distance(arc.from, arc.to) < 1e-9) {
        throw new Error('SR 原型边界不连续');
      }
      previous = arc.to;
      const edge: ReferenceArc = { from: [...arc.from], to: [...arc.to] };
      if (arc.circle !== undefined) {
        if (!Array.isArray(arc.circle) || arc.circle.length !== 3 || !arc.circle.every(finite)
          || arc.circle[2] <= 0 || typeof arc.add !== 'boolean') throw new Error('SR 原型圆参数无效');
        edge.circle = [...arc.circle] as [number, number, number];
        edge.add = arc.add;
      }
      return edge;
    });
    if (distance(previous, part.start) > 1e-7) throw new Error('SR 原型轮廓未闭合');
    return { name: part.name, start: [...part.start], arcs };
  });
  return { width: value.width, height: value.height, parts, smoothJointPoints: value.smoothJointPoints.map(p => [...p]) };
}

export function referenceCircles(geometry: ReferenceGeometry): ReferenceCircle[] {
  const circles: ReferenceCircle[] = [];
  for (const part of geometry.parts) for (const arc of part.arcs) {
    if (arc.circle) circles.push({ ...arc, circle: arc.circle, add: arc.add!, name: part.name, id: circles.length });
  }
  return circles;
}

export function polygonClip(points: Point[], geometry: Pick<ReferenceGeometry, 'width' | 'height'>): string {
  return `polygon(${points.map(p => `${p[0] / geometry.width * 100}% ${p[1] / geometry.height * 100}%`).join(',')})`;
}

export function capPolygon(arc: ReferenceArc, geometry: Pick<ReferenceGeometry, 'width' | 'height'>): Point[] {
  if (!arc.circle) return [];
  const [cx, cy] = arc.circle, a = arc.from, b = arc.to;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const side = Math.sign(dx * (cy - a[1]) - dy * (cx - a[0]));
  const signedDistance = (p: Point) => -side * (dx * (p[1] - a[1]) - dy * (p[0] - a[0])) + Math.hypot(dx, dy);
  const box: Point[] = [[0, 0], [geometry.width, 0], [geometry.width, geometry.height], [0, geometry.height]];
  const output: Point[] = [];
  for (let i = 0; i < box.length; i++) {
    const p = box[i], q = box[(i + 1) % box.length], dp = signedDistance(p), dq = signedDistance(q);
    if (dp >= 0) output.push(p);
    if ((dp >= 0) !== (dq >= 0)) {
      const t = dp / (dp - dq);
      output.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
    }
  }
  return output;
}

export function circleStyle(circle: [number, number, number], geometry: Pick<ReferenceGeometry, 'width' | 'height'>) {
  const [cx, cy, radius] = circle;
  return { left: `${(cx - radius) / geometry.width * 100}%`, top: `${(cy - radius) / geometry.height * 100}%`, width: `${2 * radius / geometry.width * 100}%` };
}

export function phaseContains(phase: ReferencePhase, arc: ReferenceArc): boolean {
  return !!arc.circle && (phase === 'final' || (phase === 'add' && !!arc.add));
}

function tangent(arc: ReferenceArc, at: Point): Point {
  const delta: Point = arc.circle ? [-(at[1] - arc.circle[1]) * (arc.add ? 1 : -1), (at[0] - arc.circle[0]) * (arc.add ? 1 : -1)]
    : [arc.to[0] - arc.from[0], arc.to[1] - arc.from[1]];
  const length = Math.hypot(...delta);
  if (length < 1e-12) throw new Error('SR 接点切线无法计算');
  return [delta[0] / length, delta[1] / length];
}

/** Recalculate both tangents at the upstream-designated smooth joints.
 * Do not filter by the resulting error: a damaged join must remain visible.
 */
export function measureReferenceTangency(geometry: ReferenceGeometry) {
  const joins = geometry.parts.flatMap(part => part.arcs.map((arc, index) => ({ arc, next: part.arcs[(index + 1) % part.arcs.length] })));
  const measurements = geometry.smoothJointPoints.map(at => {
    const join = joins.find(({ arc, next }) => distance(arc.to, at) < 1e-7 && distance(next.from, at) < 1e-7);
    if (!join) return null;
    const incoming = tangent(join.arc, at), outgoing = tangent(join.next, at);
    const dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1];
    const cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0];
    return { point: at, tangent: incoming, error: Math.atan2(Math.abs(cross), dot) * 180 / Math.PI };
  });
  const measured = measurements.filter((joint): joint is NonNullable<typeof joint> => joint !== null);
  return { joints: measured, expected: geometry.smoothJointPoints.length, complete: measured.length === measurements.length,
    maxError: measured.length ? Math.max(...measured.map(joint => joint.error)) : null };
}
