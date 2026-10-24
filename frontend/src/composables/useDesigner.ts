import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import type { DesignInput, DesignVariant } from "../engine/types";
import { DEFAULT_INPUT, DRAFT_KEY, parseDraft, type Draft } from "../lib/draft";
import DesignWorker from "../design.worker.ts?worker&inline";
import { createDesignWorkerController, type DesignErrorKind } from "../lib/design-worker-controller";

export function useDesigner() {
  let restored: Draft | null = null;
  try {
    restored = parseDraft(localStorage.getItem(DRAFT_KEY));
  } catch {
    /* Storage can be unavailable in private browsing. */
  }
  const input = reactive<DesignInput>({ ...DEFAULT_INPUT, ...restored?.input });
  const selectedId = ref(restored?.selectedId || "weave");
  const ink = ref(restored?.ink || "#21372c");
  const background = ref(restored?.background || "#b2edc4");
  const guides = ref(restored?.guides ?? true);
  const variants = ref<DesignVariant[]>([]);
  const busy = ref(true);
  const error = ref("");
  const errorKind = ref<DesignErrorKind | null>(null);
  const storageAvailable = ref(true);
  const hasDraft = ref(!!restored);
  const validation = computed(() => {
    if (!input.text.length) return "输入字母，开始设计";
    if (input.text.length > 32) return "最多输入 32 个英文字母";
    if (!/^[A-Za-z]+$/.test(input.text)) return "请输入英文字母，大小写均可";
    if (input.composition === "signature" && input.markText && !/^[A-Za-z]{1,4}$/.test(input.markText))
      return "图形字母请输入 1–4 个英文字母";
    return "";
  });
  const current = computed(
    () =>
      variants.value.find((item) => item.id === selectedId.value) ||
      variants.value[0],
  );
  const ready = computed(
    () => !!current.value && !validation.value && !error.value && !busy.value,
  );
  // The Worker program lives in this bundle, so an already-open page remains
  // usable after a deployment removes the previous build's hashed assets.
  const controller = createDesignWorkerController({
    createWorker: () => new DesignWorker({ name: "lean-design" }),
    onState(state) {
      busy.value = state.busy;
      error.value = state.error;
      errorKind.value = state.errorKind;
    },
    onResult(result) {
      variants.value = result;
      if (!variants.value.some((item) => item.id === selectedId.value))
        selectedId.value = variants.value[0]!.id;
      save();
    },
  });
  function generate(restart = false) {
    if (validation.value) {
      controller.cancel();
      return;
    }
    // Inline workers run from a blob URL; resolve assets against the page origin.
    const fontBaseUrl = new URL(`${import.meta.env.BASE_URL}fonts/`, location.origin).href;
    controller.request({ ...input }, fontBaseUrl, restart);
  }
  function save() {
    if (validation.value) return;
    const draft: Draft = {
      version: 3,
      input: { ...input },
      selectedId: selectedId.value,
      ink: ink.value,
      background: background.value,
      guides: guides.value,
    };
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
      hasDraft.value = true;
      storageAvailable.value = true;
    } catch {
      storageAvailable.value = false;
    }
  }
  watch(
    () => ({ ...input }),
    () => {
      generate();
    },
    { immediate: true },
  );
  watch([ink, background, guides, selectedId], save);
  function reset() {
    Object.assign(input, DEFAULT_INPUT);
    selectedId.value = "weave";
    ink.value = "#21372c";
    background.value = "#b2edc4";
    guides.value = true;
  }
  function shuffle() {
    input.seed = (input.seed + 1) % 1000001;
  }
  function retry() {
    generate(true);
  }
  onBeforeUnmount(() => {
    controller.dispose();
  });
  return {
    input,
    selectedId,
    ink,
    background,
    guides,
    variants,
    busy,
    error,
    errorKind,
    validation,
    current,
    ready,
    storageAvailable,
    hasDraft,
    reset,
    shuffle,
    retry,
  };
}
