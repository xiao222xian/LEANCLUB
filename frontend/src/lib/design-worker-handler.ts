import type { DesignInput, DesignVariant } from '../engine/types';
import type { WorkerRequest, WorkerResponse } from './design-worker-controller';

/** Small injectable Worker protocol, so font retries and error phases are tested
 * without a DOM. Completed results are accepted only by the main-thread owner. */
export function createDesignWorkerHandler(dependencies: {
  resolveComposition: (input: DesignInput) => string;
  initializeEngine: (fontBaseUrl: string) => Promise<void>;
  generateVariants: (input: DesignInput) => DesignVariant[];
  post: (response: WorkerResponse) => void;
}) {
  let initialization: Promise<void> | undefined;
  let initializedBase: string | undefined;
  async function prepareFonts(base: string) {
    if (initializedBase !== base) {
      initialization = undefined;
      initializedBase = base;
    }
    initialization ||= dependencies.initializeEngine(base);
    const pending = initialization;
    try { await pending; }
    catch (error) {
      if (initialization === pending) initialization = undefined;
      throw error;
    }
  }
  return async ({ id, input, fontBaseUrl }: WorkerRequest) => {
    let phase: 'load' | 'compute' = 'compute';
    try {
      if (dependencies.resolveComposition(input) !== 'monogram') {
        phase = 'load';
        if (!fontBaseUrl || !/^https?:\/\//.test(fontBaseUrl)) throw new Error('字体地址无效，请刷新页面后重试');
        await prepareFonts(fontBaseUrl);
      }
      phase = 'compute';
      const variants = dependencies.generateVariants(input);
      dependencies.post({ id, variants });
    } catch (error) {
      dependencies.post({
        id, errorKind: phase,
        error: error instanceof Error ? error.message : phase === 'load' ? '字体加载失败，请重试' : '生成失败，请调整参数后重试',
      });
    }
  };
}
