import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createDesignWorkerController, type DesignerWorkerState, type WorkerPort, type WorkerRequest, type WorkerResponse } from '../src/lib/design-worker-controller';
import { createDesignWorkerHandler } from '../src/lib/design-worker-handler';
import type { DesignInput, DesignVariant } from '../src/engine/types';

const input: DesignInput = { text: 'SR', weight: 24, overlap: 44, tension: 52, spacing: 0, seed: 0, mode: 'geometry', composition: 'auto' };
const fontBaseUrl = 'https://example.test/design/fonts/';
const artwork = (id: string): DesignVariant[] => [{
  id, name: id, text: 'SR', mode: 'geometry', paths: ['M0 0L10 0L10 10Z'],
  fillRule: 'evenodd', viewBox: [0, 0, 10, 10], bounds: [0, 0, 10, 10],
  guides: { paths: [], lines: [], circles: [], joints: [] }, placements: [], resolvedWeight: 24,
  statistics: { characters: 2, groups: 1, contours: 1, arcs: 0, segments: 3 },
}];

class FakeClock {
  now = 0;
  next = 0;
  jobs = new Map<number, { at: number; callback: () => void }>();
  history = new Map<number, () => void>();
  set = (callback: () => void, delay: number) => {
    const token = ++this.next;
    this.jobs.set(token, { at: this.now + delay, callback });
    this.history.set(token, callback);
    return token;
  };
  clear = (token: unknown) => { this.jobs.delete(token as number); };
  advance(ms: number) {
    const end = this.now + ms;
    for (;;) {
      const first = [...this.jobs.entries()].filter(([, job]) => job.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!first) break;
      this.now = first[1].at;
      this.jobs.delete(first[0]);
      first[1].callback();
    }
    this.now = end;
  }
  // Simulates a timeout already queued by the browser before clearTimeout.
  force(token: number) { this.history.get(token)!(); }
}
class FakeWorker {
  onmessage: ((event: MessageEvent<WorkerResponse>) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessageerror: (() => void) | null = null;
  requests: WorkerRequest[] = [];
  terminated = 0;
  postMessage(request: WorkerRequest) { this.requests.push(request); }
  terminate() { this.terminated++; }
  emit(response: WorkerResponse) { this.onmessage?.({ data: response } as MessageEvent<WorkerResponse>); }
  success(id = 'result') { this.emit({ id: this.requests.at(-1)!.id, variants: artwork(id) }); }
}
function setup(factory?: () => FakeWorker) {
  const clock = new FakeClock(), workers: FakeWorker[] = [], states: DesignerWorkerState[] = [], results: DesignVariant[][] = [];
  const controller = createDesignWorkerController({
    clock, debounceMs: 10, timeoutMs: 100,
    createWorker: () => {
      const worker = factory ? factory() : new FakeWorker();
      workers.push(worker);
      return worker as unknown as WorkerPort;
    },
    onState: state => states.push(state), onResult: result => results.push(result),
  });
  return { controller, clock, workers, states, results, state: () => states.at(-1)! };
}

test('rapid dragging: queued old error, result and timeout cannot kill or overwrite the replacement', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl);
  x.clock.advance(10);
  const old = x.workers[0]!, oldError = old.onerror!, oldMessage = old.onmessage!, oldTimeout = x.clock.next;
  const oldId = old.requests[0]!.id;
  x.controller.request({ ...input, overlap: 66 }, fontBaseUrl);
  x.clock.advance(10);
  const current = x.workers[1]!;
  assert.equal(old.terminated, 1);
  oldError();
  oldMessage({ data: { id: oldId, variants: artwork('stale') } } as MessageEvent<WorkerResponse>);
  x.clock.force(oldTimeout);
  assert.equal(current.terminated, 0);
  assert.equal(x.state().busy, true);
  assert.equal(x.state().error, '');
  assert.equal(x.results.length, 0);
  x.clock.advance(99);
  assert.equal(x.state().busy, true);
  x.clock.advance(1);
  assert.equal(current.terminated, 1, 'the new request still owns its timeout');
  assert.equal(x.state().errorKind, 'timeout');
});

test('debounce coalesces input, snapshots values, and ignores wrong request IDs on a reused worker', () => {
  const x = setup(), mutable = { ...input };
  x.controller.request(mutable, fontBaseUrl);
  mutable.text = 'XX';
  x.clock.advance(5);
  x.controller.request(mutable, fontBaseUrl);
  mutable.text = 'YY';
  x.clock.advance(10);
  assert.equal(x.workers.length, 1);
  const worker = x.workers[0]!;
  assert.equal(worker.requests[0]!.input.text, 'XX');
  worker.success('first');
  const firstId = worker.requests[0]!.id;
  x.controller.request(input, fontBaseUrl);
  x.clock.advance(10);
  worker.emit({ id: firstId, variants: artwork('late') });
  assert.equal(x.results.length, 1);
  assert.equal(x.state().busy, true);
  worker.success('second');
  assert.equal(x.workers.length, 1, 'an idle initialized worker is reused');
  assert.deepEqual(x.results.map(result => result[0]!.id), ['first', 'second']);
  assert.equal(x.state().busy, false);
});

test('startup error reports load, then explicit retry succeeds; a late old error stays harmless', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  const old = x.workers[0]!, error = old.onerror!;
  error();
  assert.equal(x.state().errorKind, 'load');
  assert.equal(old.terminated, 1);
  x.controller.request(input, fontBaseUrl, true); x.clock.advance(10);
  const fresh = x.workers[1]!;
  error();
  fresh.emit({ type: 'ready' }); fresh.success('recovered');
  assert.equal(fresh.terminated, 0);
  assert.equal(x.state().errorKind, null);
  assert.equal(x.results[0]![0]!.id, 'recovered');
});

test('Worker factory failure is recoverable and is classified as load', () => {
  let attempts = 0;
  const x = setup(() => { if (++attempts === 1) throw new Error('Blocked Worker'); return new FakeWorker(); });
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  assert.equal(x.state().errorKind, 'load');
  assert.equal(x.state().busy, false);
  x.controller.request(input, fontBaseUrl, true); x.clock.advance(10);
  x.workers[0]!.success('retry');
  assert.equal(x.state().error, '');
  assert.equal(x.results.length, 1);
});

test('a running Worker crash reports compute; timeout is distinct and both support retry', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  x.workers[0]!.emit({ type: 'ready' });
  x.workers[0]!.onerror!();
  assert.equal(x.state().errorKind, 'compute');
  x.controller.request(input, fontBaseUrl, true); x.clock.advance(110);
  assert.equal(x.state().errorKind, 'timeout');
  assert.equal(x.workers[1]!.terminated, 1);
  x.controller.request(input, fontBaseUrl, true); x.clock.advance(10);
  x.workers[2]!.success('recovered');
  assert.equal(x.state().errorKind, null);
});

test('compute and empty-result failures retain the last accepted artwork and can recover', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  const worker = x.workers[0]!;
  worker.success('visible');
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  worker.emit({ id: worker.requests.at(-1)!.id, error: '该组合暂时无法生成', errorKind: 'compute' });
  assert.equal(x.state().errorKind, 'compute');
  assert.equal(x.results.at(-1)![0]!.id, 'visible');
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  worker.emit({ id: worker.requests.at(-1)!.id, variants: [] });
  assert.equal(x.results.length, 1);
  assert.equal(x.state().errorKind, 'compute');
  x.controller.request(input, fontBaseUrl); x.clock.advance(10); worker.success('new');
  assert.equal(x.results.at(-1)![0]!.id, 'new');
  assert.equal(x.state().error, '');
});

test('message decoding and send failures are compute errors and retire only their own Worker', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  x.workers[0]!.onmessageerror!();
  assert.equal(x.state().errorKind, 'compute');
  assert.equal(x.workers[0]!.terminated, 1);
  const y = setup(() => { const worker = new FakeWorker(); worker.postMessage = () => { throw new Error('clone failure'); }; return worker; });
  y.controller.request(input, fontBaseUrl); y.clock.advance(10);
  assert.equal(y.state().errorKind, 'compute');
  assert.equal(y.workers[0]!.terminated, 1);
});

test('cancel and dispose prevent pending launches and all late updates', () => {
  const x = setup();
  x.controller.request(input, fontBaseUrl); x.controller.cancel(); x.clock.advance(10);
  assert.equal(x.workers.length, 0);
  x.controller.request(input, fontBaseUrl); x.clock.advance(10);
  const worker = x.workers[0]!, message = worker.onmessage!, error = worker.onerror!;
  x.controller.dispose();
  const stateCount = x.states.length;
  error(); message({ data: { id: worker.requests[0]!.id, variants: artwork('late') } } as MessageEvent<WorkerResponse>);
  x.controller.request(input, fontBaseUrl); x.clock.advance(1000);
  assert.equal(x.states.length, stateCount);
  assert.equal(x.results.length, 0);
  assert.equal(x.workers.length, 1);
  assert.equal(worker.terminated, 1);
});

function deferred() {
  let resolve!: () => void, reject!: (reason: unknown) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
test('monograms compute without font initialization or any font URL', async () => {
  let initializes = 0, computes = 0;
  const messages: WorkerResponse[] = [];
  const handle = createDesignWorkerHandler({
    resolveComposition: () => 'monogram', initializeEngine: async () => { initializes++; },
    generateVariants: () => { computes++; return artwork('geometry'); }, post: value => messages.push(value),
  });
  await handle({ id: 4, input, fontBaseUrl: '' });
  assert.equal(initializes, 0); assert.equal(computes, 1);
  assert.deepEqual(messages, [{ id: 4, variants: artwork('geometry') }]);
});

test('wordmarks and signatures share pending initialization and do not compute before fonts arrive', async () => {
  const fonts = deferred(), bases: string[] = [], messages: WorkerResponse[] = [];
  let computes = 0;
  const handle = createDesignWorkerHandler({
    resolveComposition: data => data.composition!, initializeEngine: base => { bases.push(base); return fonts.promise; },
    generateVariants: () => { computes++; return artwork('font'); }, post: value => messages.push(value),
  });
  const first = handle({ id: 1, input: { ...input, composition: 'wordmark' }, fontBaseUrl });
  const second = handle({ id: 2, input: { ...input, composition: 'signature' }, fontBaseUrl });
  assert.deepEqual(bases, [fontBaseUrl]); assert.equal(computes, 0);
  fonts.resolve(); await Promise.all([first, second]);
  assert.equal(computes, 2); assert.deepEqual(messages.map(value => 'id' in value && value.id), [1, 2]);
});

test('a rejected font promise is cleared so the same Worker can load successfully on retry', async () => {
  let attempts = 0, computes = 0;
  const messages: WorkerResponse[] = [];
  const handle = createDesignWorkerHandler({
    resolveComposition: () => 'wordmark',
    initializeEngine: async () => { if (++attempts === 1) throw new Error('字库加载失败，请重试'); },
    generateVariants: () => { computes++; return artwork('retry'); }, post: value => messages.push(value),
  });
  await handle({ id: 1, input, fontBaseUrl });
  assert.deepEqual(messages[0], { id: 1, error: '字库加载失败，请重试', errorKind: 'load' });
  assert.equal(computes, 0);
  await handle({ id: 2, input, fontBaseUrl });
  assert.equal(attempts, 2); assert.equal(computes, 1);
  assert.deepEqual(messages[1], { id: 2, variants: artwork('retry') });
});

test('invalid relative font URL is load failure; geometry failure after font loading is compute', async () => {
  const messages: WorkerResponse[] = [];
  let initializes = 0;
  const handle = createDesignWorkerHandler({
    resolveComposition: () => 'wordmark', initializeEngine: async () => { initializes++; },
    generateVariants: () => { throw new Error('轮廓组合失败'); }, post: value => messages.push(value),
  });
  await handle({ id: 1, input, fontBaseUrl: '/design/fonts/' });
  assert.equal(initializes, 0);
  assert.equal('errorKind' in messages[0]! && messages[0].errorKind, 'load');
  await handle({ id: 2, input, fontBaseUrl });
  assert.equal(initializes, 1);
  assert.deepEqual(messages[1], { id: 2, error: '轮廓组合失败', errorKind: 'compute' });
});
