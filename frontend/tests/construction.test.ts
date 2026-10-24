import test from 'node:test';
import assert from 'node:assert/strict';
import { constructionFrame, CONSTRUCTION_DURATION, CONSTRUCTION_FPS,
  CONSTRUCTION_STAGES } from '../src/lib/construction.ts';

const counts = { lines: 4, circles: 4, outlines: 4 };

test('the original representative times select all five construction stages', () => {
  assert.equal(CONSTRUCTION_DURATION, 15);
  assert.equal(CONSTRUCTION_FPS, 30);
  assert.deepEqual(CONSTRUCTION_STAGES.map(stage => stage.time), [1.5, 3.4, 7.8, 10.5, 14]);
  assert.deepEqual([1.5, 3.4, 7.8, 10.5, 14].map(time => constructionFrame(time, counts).phase), [0, 1, 2, 3, 4]);
  for (const [index, stage] of CONSTRUCTION_STAGES.entries()) {
    assert.equal(constructionFrame(stage.start, counts).phase, index);
    if (index > 0) assert.equal(constructionFrame(stage.start - .001, counts).phase, index - 1);
  }
});

test('straight edges draw sequentially before circles rather than appearing as a group', () => {
  const early = constructionFrame(.825, counts);
  assert(Math.abs(early.lines[0]!.strokeDashoffset - .5) < 1e-10);
  assert(early.lines[0]!.strokeDashoffset < early.lines[3]!.strokeDashoffset);
  assert(early.lines[0]!.opacity > early.lines[3]!.opacity);
  assert(early.circles.every(circle => circle.opacity === 0 && circle.strokeDashoffset === 1));
  const firstFinished = constructionFrame(1.4, counts);
  assert.equal(firstFinished.lines[0]!.strokeDashoffset, 0);
  assert(firstFinished.lines[3]!.strokeDashoffset > 0);
  assert(constructionFrame(2, counts).lines.every(line => line.strokeDashoffset === 0));
});

test('circles have individual delays and completed lines remain until fill begins', () => {
  const early = constructionFrame(2.4, counts);
  assert(early.circles[0]!.strokeDashoffset < 1);
  assert.equal(early.circles[3]!.strokeDashoffset, 1);
  const firstFinished = constructionFrame(2.95, counts);
  assert.equal(firstFinished.circles[0]!.strokeDashoffset, 0);
  assert(firstFinished.circles[3]!.strokeDashoffset > 0);
  assert(constructionFrame(4.2, counts).circles.every(circle => circle.strokeDashoffset === 0));
  assert(early.lines.every(line => line.strokeDashoffset === 0 && line.opacity === .75));
  assert(early.outlines.every(outline => outline.opacity === 0));
});

test('closed contours are stroked in order and tangent guides appear before filling', () => {
  const middle = constructionFrame(5.75, counts);
  assert(Math.abs(middle.outlines[0]!.strokeDashoffset - .5) < 1e-10);
  assert(middle.outlines[0]!.strokeDashoffset < middle.outlines[3]!.strokeDashoffset);
  const complete = constructionFrame(7.8, counts);
  assert(complete.outlines.every(outline => outline.strokeDashoffset === 0 && outline.opacity === 1));
  assert.equal(complete.fill.opacity, 0);
  assert.equal(complete.jointsOpacity, 1);
});

test('directional mask continues moving after opacity is full; this is not a fade-in', () => {
  const justStarted = constructionFrame(8.5, counts);
  const middle = constructionFrame(10.5, counts);
  const almostDone = constructionFrame(12, counts);
  assert.equal(justStarted.fill.opacity, 1);
  assert.equal(middle.fill.opacity, 1);
  assert.equal(almostDone.fill.opacity, 1);
  assert(justStarted.fill.masked && middle.fill.masked && almostDone.fill.masked);
  assert(middle.fill.maskStart > justStarted.fill.maskStart);
  assert(almostDone.fill.maskStart > middle.fill.maskStart);
  assert(middle.fill.maskEnd > justStarted.fill.maskEnd);
  assert(middle.fill.maskStart < middle.fill.maskEnd);
  assert(middle.fill.maskStart > 0 && middle.fill.maskStart < 1);
  assert(middle.fill.maskEnd <= 1);
  assert(middle.lines.every(line => line.opacity === 0));
  assert(middle.circles.every(circle => circle.opacity === 0));
});

test('completed construction has exact opaque black ink without a mask or guides', () => {
  assert.equal(constructionFrame(12.3, counts).fill.masked, false);
  for (const time of [14, 15]) {
    const frame = constructionFrame(time, counts);
    assert.equal(frame.fill.progress, 1);
    assert.equal(frame.fill.opacity, 1);
    assert.equal(frame.fill.color, 'rgb(18,18,18)');
    assert.equal(frame.fill.masked, false);
    assert.equal(frame.gridOpacity, 0);
    assert.equal(frame.jointsOpacity, 0);
    assert([...frame.lines, ...frame.circles, ...frame.outlines].every(stroke => stroke.opacity === 0));
  }
});

test('inspection shows complete geometry at any time without changing the artwork', () => {
  const frame = constructionFrame(0, counts, true);
  assert([...frame.lines, ...frame.circles, ...frame.outlines].every(stroke => stroke.strokeDashoffset === 0 && stroke.opacity > 0));
  assert.equal(frame.fill.opacity, .08);
  assert.equal(frame.fill.masked, false);
  assert.equal(frame.fill.color, 'rgb(18,18,18)');
  assert(frame.gridOpacity > 0 && frame.jointsOpacity > 0);
});

test('all 451 timeline frames and empty geometry have finite bounded drawing values', () => {
  for (const count of [counts, { lines: 0, circles: 0, outlines: 0 }]) {
    for (let index = 0; index <= CONSTRUCTION_DURATION * CONSTRUCTION_FPS; index++) {
      const frame = constructionFrame(index / CONSTRUCTION_FPS, count);
      const values = [frame.gridOpacity, frame.jointsOpacity, frame.fill.progress, frame.fill.opacity,
        frame.fill.maskStart, frame.fill.maskEnd,
        ...[...frame.lines, ...frame.circles, ...frame.outlines].flatMap(stroke => [stroke.opacity, stroke.strokeDashoffset])];
      assert(values.every(value => Number.isFinite(value) && value >= 0 && value <= 1));
    }
  }
});
