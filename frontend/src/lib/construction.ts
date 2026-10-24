/** The original SiliRoot construction timeline, with no rendering dependencies. */
export const CONSTRUCTION_DURATION = 15;
export const CONSTRUCTION_FPS = 30;
export const CONSTRUCTION_STAGES = [
  { name: '直边定位', start: 0, time: 1.5 },
  { name: '构造圆', start: 1.6, time: 3.4 },
  { name: '封闭轮廓', start: 4.3, time: 7.8 },
  { name: '渐进填色', start: 8, time: 10.5 },
  { name: '完整标志', start: 12.5, time: 14 },
] as const;

export interface ConstructionCounts {
  lines: number;
  circles: number;
  outlines: number;
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));
function progress(time: number, start: number, end: number) {
  const value = clamp((time - start) / (end - start));
  return value < .5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2;
}
function stroke(value: number, opacity: number) {
  return { strokeDasharray: '1', strokeDashoffset: 1 - value, opacity };
}

export function constructionFrame(seconds: number, counts: ConstructionCounts, inspect = false) {
  const time = Math.max(0, Math.min(CONSTRUCTION_DURATION, seconds));
  const phase = CONSTRUCTION_STAGES.reduce((current: number, stage, index) => time >= stage.start ? index : current, 0);
  const fade = inspect ? 1 : 1 - progress(time, 8.1, 10.4);
  const filling = inspect ? 1 : progress(time, 8, 12.3);
  const shade = Math.round(163 * (1 - filling) + 18 * filling);
  return {
    time,
    phase,
    gridOpacity: inspect ? .35 : progress(time, 0, .8) * .28 * fade,
    jointsOpacity: inspect ? .9 : progress(time, 5.6, 6.5) * (1 - progress(time, 8, 9)),
    lines: Array.from({ length: counts.lines }, (_, index) => {
      const offset = index / Math.max(1, counts.lines) * .6;
      const value = inspect ? 1 : progress(time, .25 + offset, 1.4 + offset);
      return stroke(value, value * .75 * fade);
    }),
    circles: Array.from({ length: counts.circles }, (_, index) => {
      const offset = index / Math.max(1, counts.circles) * 1.25;
      const value = inspect ? 1 : progress(time, 1.6 + offset, 2.95 + offset);
      return stroke(value, value * .55 * fade);
    }),
    outlines: Array.from({ length: counts.outlines }, (_, index) => {
      const delay = index / Math.max(1, counts.outlines) * .55;
      const value = inspect ? 1 : progress(time, 4.3 + delay, 7.2 + delay);
      return stroke(value, inspect ? .6 : value > 0 ? 1 - progress(time, 10.8, 12.6) : 0);
    }),
    fill: {
      progress: filling,
      opacity: inspect ? .08 : progress(time, 8, 8.5),
      // Construction is black ink on white paper. The final artwork renderer
      // owns user colors; this preserves contrast for every selected palette.
      color: `rgb(${shade},${shade},${shade})`,
      masked: filling < 1,
      maskStart: clamp(filling * 1.3 - .18),
      maskEnd: clamp(filling * 1.3 + .1),
    },
  };
}
