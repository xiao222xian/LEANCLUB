import type { DesignInput, DesignVariant } from '../engine/types';

export type DesignErrorKind = 'load' | 'compute' | 'timeout';
export interface WorkerRequest { id: number; input: DesignInput; fontBaseUrl: string }
export type WorkerResponse =
  | { type: 'ready' }
  | { id: number; variants?: DesignVariant[]; error?: string; errorKind?: DesignErrorKind };
export type WorkerPort = Pick<Worker, 'onmessage' | 'onerror' | 'onmessageerror' | 'postMessage' | 'terminate'>;
export interface DesignerWorkerState { busy: boolean; error: string; errorKind: DesignErrorKind | null }
interface Clock {
  set(callback: () => void, delay: number): unknown;
  clear(token: unknown): void;
}
interface Entry { port: WorkerPort; epoch: number; loaded: boolean }
interface Active { id: number; entry: Entry }
const nativeClock: Clock = {
  set: (callback, delay) => setTimeout(callback, delay),
  clear: token => clearTimeout(token as ReturnType<typeof setTimeout>),
};

/** Owns Worker identity separately from request identity. A terminated Worker's
 * queued event can never stop, clear a timeout for, or overwrite its replacement. */
export function createDesignWorkerController(options: {
  createWorker: () => WorkerPort;
  onState: (state: DesignerWorkerState) => void;
  onResult: (variants: DesignVariant[]) => void;
  clock?: Clock;
  debounceMs?: number;
  timeoutMs?: number;
}) {
  const clock = options.clock ?? nativeClock;
  let entry: Entry | undefined;
  let active: Active | undefined;
  let generation = 0, epoch = 0, disposed = false;
  let debounce: unknown, timeout: unknown;
  const isCurrent = (candidate: Entry) => !disposed && entry === candidate && entry.epoch === candidate.epoch;
  const isActive = (candidate: Entry, id: number) => isCurrent(candidate) && active?.entry === candidate && active.id === id && generation === id;
  function clearTimers() {
    clock.clear(debounce);
    clock.clear(timeout);
    debounce = timeout = undefined;
  }
  function retire(candidate = entry) {
    if (!candidate) return;
    if (entry === candidate) entry = undefined;
    if (active?.entry === candidate) active = undefined;
    candidate.port.onmessage = null;
    candidate.port.onerror = null;
    candidate.port.onmessageerror = null;
    candidate.port.terminate();
  }
  function state(busy: boolean, error = '', errorKind: DesignErrorKind | null = null) {
    if (!disposed) options.onState({ busy, error, errorKind });
  }
  function finishFailure(candidate: Entry, id: number, error: string, kind: DesignErrorKind, fatal = false) {
    if (!isActive(candidate, id)) return;
    clock.clear(timeout);
    timeout = undefined;
    active = undefined;
    if (fatal) retire(candidate);
    state(false, error, kind);
  }
  function createEntry(): Entry {
    const candidate: Entry = { port: options.createWorker(), epoch: ++epoch, loaded: false };
    entry = candidate;
    candidate.port.onmessage = (event: MessageEvent<WorkerResponse>) => {
      if (!isCurrent(candidate)) return;
      if ('type' in event.data && event.data.type === 'ready') {
        candidate.loaded = true;
        return;
      }
      if (!('id' in event.data) || !isActive(candidate, event.data.id)) return;
      candidate.loaded = true;
      const { id, variants, error, errorKind } = event.data;
      if (error) {
        finishFailure(candidate, id, error, errorKind ?? 'compute');
        return;
      }
      if (!variants?.length) {
        finishFailure(candidate, id, '没有生成有效轮廓，请调整参数', 'compute');
        return;
      }
      clock.clear(timeout);
      timeout = undefined;
      active = undefined;
      options.onResult(variants);
      state(false);
    };
    candidate.port.onerror = () => {
      if (!isCurrent(candidate)) return;
      const kind = candidate.loaded ? 'compute' : 'load';
      const message = kind === 'load' ? '设计引擎启动失败，请重试' : '本次计算中断，请重试';
      const id = active?.entry === candidate ? active.id : undefined;
      if (id !== undefined) finishFailure(candidate, id, message, kind, true);
      else retire(candidate);
    };
    candidate.port.onmessageerror = () => {
      if (!isCurrent(candidate) || active?.entry !== candidate) return;
      finishFailure(candidate, active.id, '设计结果读取失败，请重试', 'compute', true);
    };
    return candidate;
  }
  function request(input: DesignInput, fontBaseUrl: string, restart = false) {
    if (disposed) return;
    clearTimers();
    // Replaced in-flight work is cancelled. Idle initialized workers can be reused.
    if (active || restart) retire();
    const id = ++generation;
    const snapshot = { ...input };
    state(true);
    debounce = clock.set(() => {
      debounce = undefined;
      if (disposed || id !== generation) return;
      let candidate: Entry;
      try { candidate = entry ?? createEntry(); }
      catch {
        state(false, '设计引擎启动失败，请重试', 'load');
        return;
      }
      active = { id, entry: candidate };
      timeout = clock.set(() => {
        finishFailure(candidate, id, '生成时间较长，请调整参数后重试', 'timeout', true);
      }, options.timeoutMs ?? 15000);
      try { candidate.port.postMessage({ id, input: snapshot, fontBaseUrl } satisfies WorkerRequest); }
      catch { finishFailure(candidate, id, '设计参数发送失败，请重试', 'compute', true); }
    }, options.debounceMs ?? 130);
  }
  function cancel() {
    if (disposed) return;
    generation++;
    clearTimers();
    if (active) retire();
    state(false);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    generation++;
    clearTimers();
    retire();
  }
  return { request, cancel, dispose };
}
