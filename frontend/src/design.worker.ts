import { initializeEngine, generateVariants, resolveComposition } from './engine';
import { createDesignWorkerHandler } from './lib/design-worker-handler';
import type { WorkerRequest, WorkerResponse } from './lib/design-worker-controller';

const post = (response: WorkerResponse) => self.postMessage(response);
const handle = createDesignWorkerHandler({ initializeEngine, generateVariants, resolveComposition, post });
self.onmessage = (event: MessageEvent<WorkerRequest>) => { void handle(event.data); };
post({ type: 'ready' });
