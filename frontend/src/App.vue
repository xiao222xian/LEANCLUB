<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import StudioIcon from "./components/StudioIcon.vue";
import LogoCanvas from "./components/LogoCanvas.vue";
import ConstructionStudio from "./components/ConstructionStudio.vue";
import ReferenceLab from './components/ReferenceLab.vue';
import { useDesigner } from "./composables/useDesigner";
import { DEFAULT_INPUT } from "./lib/draft";
import type { DesignComposition } from "./engine/types";
import { exportArtwork } from "./lib/export";

const {
  input,
  selectedId,
  ink,
  background,
  variants,
  busy,
  error,
  validation,
  current,
  ready,
  storageAvailable,
  reset,
  shuffle,
  retry,
} = useDesigner();
const examples = ["Bj", "LEAN", "LeanSchool", "OpenStudio"];
const originalExamples = [
  { label: "圆弧与负空间", values: ["SR", "RS", "OG", "BQ"] },
  { label: "直边与斜线", values: ["AI", "TL", "NX", "KM"] },
  { label: "更多轮廓关系", values: ["CD", "PU", "VW", "YZ"] },
];
const modes: { id: DesignComposition; label: string }[] = [
  { id: "auto", label: "自动" }, { id: "monogram", label: "图形标" },
  { id: "wordmark", label: "字标" }, { id: "signature", label: "组合标" },
];
const isLettering = computed(() => input.composition === "wordmark" || input.composition === "signature" || ((!input.composition || input.composition === "auto") && input.text.length > 2));
const studio = ref<InstanceType<typeof ConstructionStudio>>();
const artArea = ref<HTMLElement>();
const advanced = ref(false);
const detailCandidates = ref(true);
const labOpen = ref(false);
const relations: Record<string, { tag: string; logic: string }> = {
  weave: { tag: "交替切除", logic: "在交叉处交替切开前后层，留下真实间隙。" },
  fusion: { tag: "合并轮廓", logic: "合并相交实体，保留共同的内外边界。" },
  counter: {
    tag: "相交挖空",
    logic: "从实体中切除重叠区域，让负空间构成图形。",
  },
  axis: { tag: "共用主轴", logic: "压合字母主轴，形成更紧密的共形结构。" },
  embrace: { tag: "内外嵌合", logic: "缩放并嵌入相邻字母，以留白区分内外层。" },
  tension: {
    tag: "旋转咬合",
    logic: "旋转字母后重新求交，以斜向切口建立关系。",
  },
};
const sliders = computed(() => [
  { key: "weight", label: isLettering.value ? "字形重量" : "笔画重量", min: 14, max: 38, suffix: "" },
  { key: "spacing", label: "字母间距", min: -20, max: 80, suffix: "" },
  { key: "overlap", label: isLettering.value ? "连接深度" : "重叠强度", min: 24, max: 68, suffix: "%" },
  { key: "tension", label: isLettering.value ? "切口强度" : "关系张力", min: 0, max: 100, suffix: "%" },
] as const);
const themes = [
  { name: "薄荷", background: "#b2edc4", ink: "#21372c" },
  { name: "墨黑", background: "#ffffff", ink: "#151a17" },
  { name: "深绿", background: "#21372c", ink: "#b2edc4" },
  { name: "纸白", background: "#f1ede3", ink: "#393b31" },
];
const chosenIndex = computed(() =>
  Math.max(
    0,
    variants.value.findIndex((v) => v.id === current.value?.id),
  ),
);
const number = (n: number) => String(n).padStart(2, "0");
const siteUrl = (path: string) =>
  import.meta.env.DEV ? "http://127.0.0.1:4180" + path : path;
function useExample(text: string, original = false) {
  Object.assign(input, DEFAULT_INPUT, { text, composition: original ? 'monogram' : 'auto', markText: undefined, focusIndex: undefined });
  selectedId.value = "weave";
  if (original) studio.value?.showProcess();
  else studio.value?.showFinal();
  if (window.matchMedia('(max-width: 800px)').matches) nextTick(() => artArea.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}
function chooseMode(mode: DesignComposition) {
  input.composition = mode;
  studio.value?.showFinal();
}
function restoreParameters() {
  const text = input.text, composition = input.composition, markText = input.markText;
  Object.assign(input, DEFAULT_INPUT, { text, composition, markText });
}
const exportOpen = ref(false),
  exporting = ref(false),
  exportError = ref("");
const format = ref<"svg" | "png">("svg"),
  exportWidth = ref(2048),
  withBackground = ref(false);
const exportPanel = ref<HTMLElement>(),
  exportTrigger = ref<HTMLButtonElement>();
const toast = ref("");
let toastTimer: ReturnType<typeof setTimeout> | undefined;
function notify(message: string) {
  clearTimeout(toastTimer);
  toast.value = message;
  toastTimer = setTimeout(() => (toast.value = ""), 2500);
}
async function openExport() {
  if (!ready.value) return;
  exportError.value = "";
  exportOpen.value = true;
  await nextTick();
  exportPanel.value?.querySelector<HTMLButtonElement>("button")?.focus();
}
async function closeExport() {
  if (exporting.value) return;
  exportOpen.value = false;
  await nextTick();
  exportTrigger.value?.focus();
}
function dialogKeys(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeExport();
  }
  if (event.key !== "Tab") return;
  const controls = [
    ...(exportPanel.value?.querySelectorAll<HTMLElement>(
      "button:not([disabled]),input:not([disabled]),select:not([disabled])",
    ) || []),
  ];
  const first = controls[0],
    last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
async function download() {
  if (!current.value || !ready.value) return;
  exporting.value = true;
  exportError.value = "";
  try {
    await exportArtwork(current.value, {
      format: format.value,
      color: ink.value,
      background: withBackground.value ? background.value : undefined,
      width: exportWidth.value,
    });
    notify(format.value.toUpperCase() + " 已导出");
  } catch (e) {
    exportError.value = e instanceof Error ? e.message : "导出失败，请重试";
  } finally {
    exporting.value = false;
  }
}
watch(
  exportOpen,
  (value) => (document.body.style.overflow = value ? "hidden" : ""),
);
onBeforeUnmount(() => {
  clearTimeout(toastTimer);
  document.body.style.overflow = "";
});
const filing =
  location.hostname === "boji.tensorbot.xyz" ? "辽ICP备2026010731号-1" : "";
</script>

<template>
  <div class="studio-shell">
    <a class="skip-link" href="#workbench">跳到设计工作台</a>
    <header class="studio-header">
      <a class="wordmark" :href="siteUrl('/')" aria-label="LEAN SCHOOL 学院首页">LEAN SCHOOL</a>
      <nav aria-label="站点导航"><a :href="siteUrl('/')">学院首页 ↗</a><a href="/design/" aria-current="page">薄肌设计</a></nav>
      <button ref="exportTrigger" class="button button--dark" :disabled="!ready" @click="openExport">导出作品 <StudioIcon name="arrow" :size="16" /></button>
    </header>
    <main id="workbench">
      <div class="studio-heading">
        <div><p class="eyebrow">LEAN SCHOOL / DESIGN STUDIO</p><h1>薄肌设计<span>↗</span></h1></div>
        <div class="heading-right"><span class="brand-manifesto">LEAN BODY.<br>STRONG MIND.</span><span class="heading-note"><i class="status-dot"></i>{{ storageAvailable ? '本机保存' : '未保存' }}</span></div>
      </div>
      <div class="workbench">
        <aside class="controls" aria-label="设计参数">
          <div class="input-section">
            <div class="section-label"><label for="design-text">你的名字</label><span>{{ input.text.length }} / 32</span></div>
            <input id="design-text" v-model="input.text" @input="input.focusIndex = undefined" :style="{ fontSize: Math.max(21, Math.min(41, 245 / Math.max(1, input.text.length) * 1.65)) + 'px' }" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" placeholder="LeanSchool" :aria-invalid="!!validation" aria-describedby="input-message" />
            <p id="input-message" class="input-message" :class="{ 'input-message--error': validation }">{{ validation || '英文字母 · 保留大小写' }}</p>
            <div class="examples" aria-label="示例名称"><button v-for="text in examples" :key="text" :class="{ active: input.text === text }" @click="useExample(text)">{{ text }}</button></div>
          </div>
          <div class="mode-section"><div class="section-label"><span>设计形式</span></div><div class="mode-picker" aria-label="设计形式"><button v-for="mode in modes" :key="mode.id" :aria-pressed="(input.composition || 'auto') === mode.id" @click="chooseMode(mode.id)">{{ mode.label }}</button></div>
            <p class="mode-hint">{{ isLettering ? (input.composition === 'signature' ? '缩写图形 + 完整名称' : '完整字标 · 局部连字与切口') : '几何共形 · 适合 1–2 个字母' }}</p>
            <label v-if="input.composition === 'signature'" class="mark-input">图形缩写<input v-model="input.markText" maxlength="4" :placeholder="current?.signature?.markText || '自动提取'" autocomplete="off" aria-label="图形缩写" /><span>{{ input.markText ? '自定缩写' : '按大小写词首提取' }}</span></label>
            <p v-if="input.composition === 'monogram' && input.text.length > 2" class="mode-hint">名称较长，字标或组合标更易辨认。</p>
            <div v-if="isLettering && !validation" class="focus-section"><div class="section-label"><span>局部设计</span><span>点选字母</span></div><div class="focus-letters" aria-label="选择局部设计位置"><button :aria-pressed="input.focusIndex === undefined" @click="input.focusIndex = undefined">自动</button><button v-for="(letter, index) in input.text" :key="index" :aria-label="`第 ${index + 1} 位 ${letter}`" :aria-pressed="input.focusIndex === index" :class="{ accented: current?.modifiedIndices?.includes(index) }" @click="input.focusIndex = index">{{ letter }}</button></div></div>
          </div>
          <div class="sliders">
            <div v-for="slider in sliders.slice(0, 2)" :key="slider.key" class="slider-field"><label :for="slider.key">{{ slider.label }}<output>{{ input[slider.key] }}{{ slider.suffix }}</output></label><input :id="slider.key" v-model.number="input[slider.key]" type="range" :min="slider.min" :max="slider.max" step="1" /></div>
            <button class="details-trigger" :aria-expanded="advanced" @click="advanced = !advanced">细调关系 <span>{{ advanced ? '−' : '+' }}</span></button>
            <div v-if="advanced" class="advanced-sliders"><div v-for="slider in sliders.slice(2)" :key="slider.key" class="slider-field"><label :for="slider.key">{{ slider.label }}<output>{{ input[slider.key] }}{{ slider.suffix }}</output></label><input :id="slider.key" v-model.number="input[slider.key]" type="range" :min="slider.min" :max="slider.max" step="1" /></div></div>
          </div>
          <div class="color-section"><div class="section-label"><span>配色</span><label class="custom-color-label">自选<input v-model="ink" type="color" aria-label="自选标志颜色" /><input v-model="background" type="color" aria-label="自选画布颜色" /></label></div><div class="palette"><button v-for="theme in themes" :key="theme.name" :aria-label="theme.name + '配色'" :aria-pressed="ink === theme.ink && background === theme.background" :style="{ background: theme.background, color: theme.ink }" @click="ink = theme.ink; background = theme.background">Aa</button></div></div>
          <div class="control-bottom"><button class="button button--outline" :disabled="!ready" @click="shuffle"><StudioIcon name="refresh" :size="14" />换一组关系</button><button class="text-button" @click="reset" aria-label="重置设计参数">重置</button></div>
          <details class="original-examples"><summary>共形练习 <span>12 组 ↗</span></summary><div v-for="group in originalExamples" :key="group.label"><p>{{ group.label }}</p><div class="examples"><button v-for="text in group.values" :key="text" @click="useExample(text, true)">{{ text }}</button></div></div></details>
        </aside>
        <section ref="artArea" class="art-area" aria-label="作品与候选方案">
          <div class="selected-heading"><div><span class="selected-number">{{ number(chosenIndex + 1) }}</span><h2>{{ current?.name || '字形研究' }}</h2></div><span>{{ current?.layout === 'signature' ? '组合标' : current?.layout === 'wordmark' ? '字标' : '图形标' }}<i></i>{{ input.text }}</span></div>
          <div class="stage-wrapper" :aria-busy="busy"><ConstructionStudio ref="studio" :variant="current" :color="ink" :background="background" :busy="busy" /></div>
          <div v-if="error" class="error-notice" role="alert"><div><strong>本次调整未完成</strong><p>{{ error }}{{ current ? '；画布保留上一次结果。' : '' }}</p></div><button class="text-button" @click="retry">重试</button><button class="text-button" @click="restoreParameters">恢复参数</button></div>
          <div class="variant-heading"><h2>探索方向<span>06</span></h2><div v-if="isLettering" class="candidate-view"><button :aria-pressed="detailCandidates" @click="detailCandidates = true">局部细节</button><button :aria-pressed="!detailCandidates" @click="detailCandidates = false">完整名称</button></div><p v-else>{{ current ? relations[current.id]?.logic : '' }}</p></div>
          <div class="variant-list" role="group" aria-label="六种设计方向"><button v-for="(variant, index) in variants" :key="variant.id" class="variant" :class="{ selected: current?.id === variant.id }" :disabled="!ready" :aria-pressed="current?.id === variant.id" :aria-label="variant.name" @click="selectedId = variant.id"><div class="variant-art"><LogoCanvas :variant="variant" color="#21372c" :detail="isLettering && detailCandidates" thumbnail /></div><span class="variant-caption"><span><small>{{ number(index + 1) }}</small>{{ variant.name }}</span><span class="variant-check">{{ current?.id === variant.id ? '↗' : '+' }}</span></span></button><template v-if="!variants.length"><div v-for="i in 6" :key="i" class="variant-skeleton"></div></template></div>
          <p v-if="current?.detail" class="design-detail">{{ current.detail }}</p>
        </section>
      </div>
      <details class="reference-section" @toggle="labOpen = ($event.target as HTMLDetailsElement).open"><summary><span>构造实验室<small>SR 原型 · 原图对照 / 实体拆解 / 切线检查</small></span><span class="reference-arrow">{{ labOpen ? '−' : '+' }}</span></summary><ReferenceLab v-if="labOpen" /></details>
    </main>
    <footer class="studio-footer"><a :href="siteUrl('/')">LEAN SCHOOL</a><span>多运动。多思考。做有复利的事。</span><a v-if="filing" href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">{{ filing }}</a><span class="footer-end">薄肌学院 / 设计</span></footer>
    <div v-if="toast" class="toast" role="status"><StudioIcon name="check" :size="16" />{{ toast }}</div>
    <Teleport to="body"
      ><div v-if="exportOpen" class="modal-backdrop" @click.self="closeExport">
        <section
          ref="exportPanel"
          class="export-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="export-title"
          @keydown="dialogKeys"
        >
          <div class="export-header">
            <h2 id="export-title">导出作品</h2>
            <button
              class="icon-button"
              aria-label="关闭导出"
              :disabled="exporting"
              @click="closeExport"
            >
              <StudioIcon name="close" />
            </button>
          </div>
          <div
            class="export-preview"
            :class="{ 'export-preview--transparent': !withBackground }"
            :style="withBackground ? { background } : {}"
          >
            <LogoCanvas :variant="current" :color="ink" thumbnail />
          </div>
          <div class="segmented">
            <button
              :aria-pressed="format === 'svg'"
              :class="{ active: format === 'svg' }"
              @click="format = 'svg'"
            >
              SVG <small>矢量</small></button
            ><button
              :aria-pressed="format === 'png'"
              :class="{ active: format === 'png' }"
              @click="format = 'png'"
            >
              PNG <small>图片</small>
            </button>
          </div>
          <label v-if="format === 'png'" class="export-option"
            >图片宽度<select v-model.number="exportWidth">
              <option :value="1280">1280 px</option>
              <option :value="2048">2048 px</option>
              <option :value="4096">4096 px</option>
            </select></label
          >
          <label class="export-option"
            >保留画布背景<input v-model="withBackground" type="checkbox"
          /></label>
          <p class="export-note">
            {{
              format === "svg"
                ? "闭合矢量轮廓 · 可继续编辑"
                : "透明背景 · 保留真实镂空"
            }}
          </p>
          <p v-if="exportError" class="export-error" role="alert">
            {{ exportError }}
          </p>
          <button
            class="button button--dark export-download"
            :disabled="exporting || !ready"
            @click="download"
          >
            {{ exporting ? "正在导出…" : "下载 " + format.toUpperCase()
            }}<StudioIcon name="download" />
          </button>
        </section></div
    ></Teleport>
  </div>
</template>
