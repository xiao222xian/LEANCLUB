<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';
import type { DesignVariant } from '../engine/types';
import { constructionFrame, CONSTRUCTION_DURATION as DURATION,
  CONSTRUCTION_FPS as FPS, CONSTRUCTION_STAGES as stages } from '../lib/construction';
import LogoCanvas from './LogoCanvas.vue';
import StudioIcon from './StudioIcon.vue';
import BrandApplications from './BrandApplications.vue';

const props = defineProps<{
  variant?: DesignVariant;
  color: string;
  background: string;
  busy?: boolean;
}>();

const id = `construction-${useId().replace(/:/g, '')}`;
const surface = ref<HTMLElement>();
const view = ref<'process' | 'final' | 'application'>('final');
const monochrome = ref(false);
const time = ref(0), playing = ref(false), loop = ref(true), rate = ref(1);
const inspect = ref(false), showCircles = ref(true), showLines = ref(true), showJoints = ref(true);
const selectedCircle = ref(-1), reducedMotion = ref(false);
let frame = 0, last = 0, visible = true;
let observer: IntersectionObserver | undefined;
let motionQuery: MediaQueryList | undefined;

const box = computed(() => props.variant?.viewBox ?? [0, 0, 320, 200]);
const unit = computed(() => Math.min(box.value[2] / 320, box.value[3] / 200));
const circles = computed(() => props.variant?.guides.circles ?? []);
const lines = computed(() => props.variant?.guides.lines ?? []);
const joints = computed(() => (props.variant?.guides as
  (DesignVariant['guides'] & { joints?: { point: number[]; tangent: number[] }[] }) | undefined)?.joints ?? []);
const outlinePaths = computed(() => {
  const variant = props.variant;
  if (!variant) return [];
  // The legacy adapter joins contours for even-odd filling. Use the identical
  // contours separately for the original staggered drawing sequence.
  if (variant.guides.paths.join(' ') === variant.paths.join(' ')) return variant.guides.paths;
  return variant.paths;
});
const selected = computed(() => circles.value[selectedCircle.value]);
const isLettering = computed(() => props.variant?.layout === 'wordmark' || props.variant?.layout === 'signature');
const stageNames = computed(() => isLettering.value ? ['字形基线', '局部关系', '轮廓成形', '渐进填色', '完整字标'] : stages.map(stage => stage.name));
const previewSizes = computed(() => isLettering.value ? [240, 160, 96] : [96, 48, 32]);
const motion = computed(() => constructionFrame(time.value, {
  lines: lines.value.length, circles: circles.value.length, outlines: outlinePaths.value.length,
}, inspect.value));
const phase = computed(() => motion.value.phase);
const workspace = computed(() => ({
  x: box.value[0] + box.value[2] * 12 / 320,
  y: box.value[1] + box.value[3] * 18 / 200,
  width: box.value[2] * 296 / 320,
  height: box.value[3] * 158 / 200,
}));
const circleLabel = (index: number) => `C${String(index + 1).padStart(2, '0')}`;
const number = (value: number) => Number(value.toFixed(2)).toString();
function restart() {
  inspect.value = false;
  time.value = reducedMotion.value ? DURATION : 0;
  playing.value = !reducedMotion.value;
  last = performance.now();
}
function play() {
  inspect.value = false;
  if (time.value >= DURATION) time.value = 0;
  playing.value = !playing.value;
  last = performance.now();
}
function replay() {
  view.value = 'process'; inspect.value = false; time.value = 0;
  playing.value = true; last = performance.now();
}
function showProcess() { view.value = 'process'; restart(); }
function showFinal() { view.value = 'final'; }
defineExpose({ showProcess, showFinal });
function seek(seconds: number) {
  playing.value = false; inspect.value = false;
  time.value = Math.max(0, Math.min(DURATION, seconds));
}
function toggleInspection() {
  inspect.value = !inspect.value;
  if (inspect.value) playing.value = false;
}
function chooseCircle(index: number) {
  selectedCircle.value = index; inspect.value = true; playing.value = false;
}
function motionChanged() {
  reducedMotion.value = motionQuery?.matches ?? false;
  if (reducedMotion.value) { playing.value = false; time.value = DURATION; }
}
function visibilityChanged() { last = performance.now(); }
function tick(now: number) {
  frame = requestAnimationFrame(tick);
  if (now - last < 1000 / FPS) return;
  const delta = Math.max(0, Math.min(.1, (now - last) / 1000));
  last = now;
  if (!playing.value || !visible || document.hidden || view.value !== 'process' || inspect.value || props.busy || !props.variant) return;
  time.value += delta * rate.value;
  if (time.value >= DURATION) {
    if (loop.value) time.value %= DURATION;
    else { time.value = DURATION; playing.value = false; }
  }
}
watch(() => props.variant, () => { selectedCircle.value = -1; restart(); });
watch(view, () => { last = performance.now(); });
watch(showCircles, value => { if (!value) selectedCircle.value = -1; });
onMounted(() => {
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  motionChanged(); restart();
  motionQuery.addEventListener('change', motionChanged);
  document.addEventListener('visibilitychange', visibilityChanged);
  if ('IntersectionObserver' in window && surface.value) {
    observer = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? false; last = performance.now();
    }, { threshold: .15 });
    observer.observe(surface.value);
  }
  frame = requestAnimationFrame(tick);
});
onBeforeUnmount(() => {
  cancelAnimationFrame(frame); observer?.disconnect();
  motionQuery?.removeEventListener('change', motionChanged);
  document.removeEventListener('visibilitychange', visibilityChanged);
});
</script>

<template>
  <section class="construction-studio" aria-label="标志构造工作台">
    <div class="studio-topbar">
      <div class="studio-views" aria-label="预览视图">
        <button type="button" :aria-pressed="view === 'final'" @click="showFinal">最终标志</button>
        <button type="button" :aria-pressed="view === 'process'" @click="showProcess">构造过程</button>
        <button type="button" :aria-pressed="view === 'application'" @click="view = 'application'">应用预览</button>
      </div>
      <button v-if="view === 'process'" type="button" class="inspection-toggle" :aria-pressed="inspect" :disabled="!variant" @click="toggleInspection">
        <StudioIcon name="grid" :size="15" />检查几何
      </button>
      <button v-else-if="view === 'final'" type="button" class="inspection-toggle" :aria-pressed="monochrome" @click="monochrome = !monochrome">{{ monochrome ? '恢复配色' : '纯黑结果' }}</button>
    </div>

    <BrandApplications v-if="view === 'application' && variant" :variant="variant" :color="color" :background="background" />
    <div v-show="view !== 'application'" ref="surface" class="studio-surface" :class="{ 'is-final': view === 'final', 'is-busy': busy }" :style="{ background: view === 'process' || monochrome ? '#fff' : background }" :aria-busy="busy">
      <svg v-if="variant && view === 'process'" class="process-art" :viewBox="box.join(' ')" role="img" :aria-label="`${variant.text} · ${inspect ? '几何检查' : stages[phase]!.name}`">
        <defs>
          <clipPath :id="`${id}-workspace`"><rect v-bind="workspace" /></clipPath>
          <pattern :id="`${id}-grid`" :x="box[0]" :y="box[1]" :width="12.5 * unit" :height="12.5 * unit" patternUnits="userSpaceOnUse">
            <path :d="`M ${12.5 * unit} 0 H 0 V ${12.5 * unit}`" fill="none" stroke="#9bacaa" :stroke-width=".3 * unit" />
          </pattern>
          <linearGradient :id="`${id}-sweep`" gradientUnits="userSpaceOnUse"
            :x1="box[0] + box[2] * 70 / 320" :y1="box[1] + box[3] * 25 / 200"
            :x2="box[0] + box[2] * 245 / 320" :y2="box[1] + box[3] * 170 / 200">
            <stop :offset="motion.fill.maskStart" stop-color="white" />
            <stop :offset="motion.fill.maskEnd" stop-color="black" />
          </linearGradient>
          <mask :id="`${id}-reveal`" maskUnits="userSpaceOnUse" :x="box[0]" :y="box[1]" :width="box[2]" :height="box[3]" style="mask-type:luminance">
            <rect :x="box[0]" :y="box[1]" :width="box[2]" :height="box[3]" :fill="`url(#${id}-sweep)`" />
          </mask>
        </defs>
        <rect v-bind="workspace" :fill="`url(#${id}-grid)`" :opacity="motion.gridOpacity" />
        <g :clip-path="`url(#${id}-workspace)`">
          <g v-if="showLines" fill="none" stroke="#658486" :stroke-width=".2125 * unit">
            <path v-for="(line, index) in lines" :key="`line-${index}`" :d="`M ${line[0]} ${line[1]} L ${line[2]} ${line[3]}`" pathLength="1" :style="motion.lines[index]" />
          </g>
          <g v-if="showCircles" fill="none" stroke="#748d8a" :stroke-width=".2125 * unit">
            <circle v-for="(circle, index) in circles" :key="`circle-${index}`" :cx="circle[0]" :cy="circle[1]" :r="circle[2]" :transform="`rotate(-90 ${circle[0]} ${circle[1]})`" pathLength="1" :style="motion.circles[index]" />
          </g>
          <g v-if="showJoints" stroke="#517e74" :stroke-width=".2 * unit" :opacity="motion.jointsOpacity">
            <g v-for="(joint, index) in joints" :key="`joint-${index}`">
              <line :x1="joint.point[0]! - joint.tangent[0]! * 4 * unit" :y1="joint.point[1]! - joint.tangent[1]! * 4 * unit" :x2="joint.point[0]! + joint.tangent[0]! * 4 * unit" :y2="joint.point[1]! + joint.tangent[1]! * 4 * unit" />
              <circle :cx="joint.point[0]" :cy="joint.point[1]" :r=".55 * unit" fill="white" />
            </g>
          </g>
        </g>
        <g fill="none" stroke="#303c3c" :stroke-width=".3375 * unit" stroke-linejoin="round">
          <path v-for="(path, index) in outlinePaths" :key="`outline-${index}`" :d="path" pathLength="1" :style="motion.outlines[index]" />
        </g>
        <g :mask="motion.fill.masked ? `url(#${id}-reveal)` : undefined" :fill="motion.fill.color" :fill-rule="variant.fillRule" :opacity="motion.fill.opacity">
          <path v-for="(path, index) in variant.paths" :key="`fill-${index}`" :d="path" />
        </g>
        <g v-if="inspect && showCircles" :clip-path="`url(#${id}-workspace)`" fill="none">
          <circle v-for="(circle, index) in circles" :key="`select-${index}`" :cx="circle[0]" :cy="circle[1]" :r="circle[2]" stroke="transparent" :stroke-width="4 * unit" class="circle-hit" @click="chooseCircle(index)" />
          <g v-if="selected" stroke="#276445" :stroke-width=".5 * unit" pointer-events="none">
            <circle :cx="selected[0]" :cy="selected[1]" :r="selected[2]" />
            <line :x1="selected[0]" :y1="selected[1]" :x2="selected[0]! + selected[2]!" :y2="selected[1]" stroke-dasharray="2 2" />
            <path :d="`M ${selected[0]! - 2 * unit} ${selected[1]} H ${selected[0]! + 2 * unit} M ${selected[0]} ${selected[1]! - 2 * unit} V ${selected[1]! + 2 * unit}`" />
          </g>
        </g>
      </svg>
      <LogoCanvas v-else-if="variant" :variant="variant" :color="monochrome ? '#151a17' : color" />
      <div v-else class="empty-canvas">{{ busy ? '正在构造' : '输入字母开始' }}</div>
      <div class="canvas-caption" aria-hidden="true">
        <span>{{ inspect && view === 'process' ? '几何检查' : view === 'process' ? `${String(phase + 1).padStart(2, '0')} / ${stageNames[phase]}` : 'LEAN BODY. STRONG MIND.' }}</span>
        <span>{{ view === 'process' && variant ? `${circles.length} C · ${lines.length} L` : '薄肌学院 / DESIGN' }}</span>
      </div>
      <span v-if="busy && variant" class="busy-label" role="status">构造中</span>
    </div>

    <div v-if="view === 'process'" class="process-controls">
      <div class="transport">
        <button class="play-button" type="button" :disabled="!variant || busy" :aria-label="playing && !inspect ? '暂停' : '播放'" @click="play"><StudioIcon :name="playing && !inspect ? 'pause' : 'play'" :size="16" /></button>
        <button class="icon-button" type="button" :disabled="!variant || busy" aria-label="重播" title="重播" @click="replay"><StudioIcon name="refresh" :size="15" /></button>
        <label class="timeline"><span class="sr-only">构造进度</span><input type="range" min="0" :max="DURATION * FPS" step="1" :value="Math.round(time * FPS)" :disabled="!variant || busy" :aria-valuetext="`${time.toFixed(1)} 秒，${stages[phase]!.name}`" @input="seek(Number(($event.target as HTMLInputElement).value) / FPS)" /></label>
        <output class="time-label">{{ time.toFixed(1) }}<span> / 15s</span></output>
        <label class="rate-control"><span class="sr-only">播放速度</span><select v-model.number="rate"><option :value=".5">0.5×</option><option :value="1">1×</option><option :value="1.5">1.5×</option></select></label>
        <button type="button" class="loop-button" :aria-pressed="loop" @click="loop = !loop">循环</button>
      </div>
      <div class="chapters" aria-label="构造阶段">
        <button v-for="(stage, index) in stages" :key="stage.name" type="button" :disabled="!variant || busy" :aria-pressed="phase === index && !inspect" @click="seek(stage.time)"><span>{{ String(index + 1).padStart(2, '0') }}</span>{{ stageNames[index] }}</button>
      </div>
      <div v-if="inspect" class="geometry-inspector">
        <div class="guide-switches">
          <label><input v-model="showLines" type="checkbox" />直线 <span>{{ lines.length }}</span></label>
          <label><input v-model="showCircles" type="checkbox" />圆 <span>{{ circles.length }}</span></label>
          <label><input v-model="showJoints" type="checkbox" />相切点 <span>{{ joints.length }}</span></label>
        </div>
        <div v-if="circles.length && showCircles" class="circle-measurement">
          <label><span class="sr-only">选择构造圆</span><select v-model.number="selectedCircle"><option :value="-1">选择构造圆</option><option v-for="(_, index) in circles" :key="index" :value="index">{{ circleLabel(index) }}</option></select></label>
          <output v-if="selected">中心 ({{ number(selected[0]!) }}, {{ number(selected[1]!) }})<span>R {{ number(selected[2]!) }} · 画布单位</span></output>
          <span v-else class="measurement-hint">点选圆线或从列表选择</span>
        </div>
      </div>
    </div>

    <div v-if="variant && view === 'process'" class="small-previews" :class="{ 'lettering-previews': isLettering }" aria-label="小尺寸预览">
      <span class="preview-label">小尺寸</span>
      <div v-for="size in previewSizes" :key="size" class="size-preview">
        <div class="size-art" :style="{ width: `${size}px`, height: `${size * box[3] / box[2]}px`, background }"><LogoCanvas :variant="variant" :color="color" thumbnail /></div>
        <span>{{ size }} px</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.construction-studio{width:100%;min-width:0;color:#254235;background:#fff;border:1px solid #dce4dc;border-radius:3px;overflow:hidden;font:13px/1.5 Arial,"PingFang SC","Microsoft YaHei",sans-serif}
.construction-studio button,.construction-studio select{font:inherit;color:inherit}
.construction-studio button{cursor:pointer}.construction-studio button:disabled{cursor:default;opacity:.4}
.construction-studio button:focus-visible,.construction-studio input:focus-visible,.construction-studio select:focus-visible{outline:2px solid #307953;outline-offset:3px}
.studio-topbar{height:56px;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 22px;border-bottom:1px solid #edf0eb}
.studio-views{display:flex;align-self:stretch;gap:24px}.studio-views button{border:0;border-bottom:2px solid transparent;background:none;padding:0;color:#87918b;white-space:nowrap}
.studio-views button[aria-pressed=true]{border-color:#254235;color:#254235;font-weight:600}
.inspection-toggle{display:flex;align-items:center;gap:7px;border:0;background:none;padding:7px 0;font-size:12px!important;color:#718077!important}.inspection-toggle[aria-pressed=true]{color:#254235!important}
.artwork-label{font-size:11px;color:#87918b}.studio-surface{position:relative;box-sizing:border-box;height:clamp(300px,29vw,420px);min-height:0;width:100%;display:flex;align-items:center;justify-content:center;padding:14px 16px 32px;overflow:hidden;transition:background .2s}
.process-art,.studio-surface :deep(.logo-art){display:block;width:100%;height:100%;min-height:0;max-height:none;object-fit:contain;overflow:visible}
.canvas-caption{position:absolute;bottom:15px;left:24px;right:24px;display:flex;justify-content:space-between;gap:12px;color:#849189;font-size:9px;letter-spacing:.12em;pointer-events:none}
.is-final .canvas-caption{mix-blend-mode:difference;color:#e5e9e5}.busy-label{position:absolute;top:14px;right:20px;background:#eef7ef;color:#43634e;border-radius:2px;padding:4px 8px;font-size:11px}.empty-canvas{color:#88928b;font-size:13px;height:100%;min-height:0;display:flex;align-items:center}.circle-hit{cursor:crosshair;pointer-events:stroke}
.process-controls{border-top:1px solid #edf0eb}.transport{display:flex;align-items:center;gap:12px;padding:15px 22px}.play-button{width:30px;height:30px;flex-shrink:0;display:grid;place-items:center;border:0;background:#254235;color:#fff!important;border-radius:50%}.icon-button{display:grid;place-items:center;border:0;background:none;width:24px;height:28px;padding:0;color:#75827a!important}
.timeline{flex:1;min-width:35px;display:flex;align-items:center}.timeline input{width:100%;margin:0;height:20px;padding:0;accent-color:#356b4c;cursor:pointer}.time-label{font-variant-numeric:tabular-nums;font-size:11px;white-space:nowrap;min-width:66px}.time-label span{color:#99a29b}.rate-control select{border:0;background:none;padding:2px;font-size:11px;cursor:pointer}.loop-button{padding:4px 7px;border:1px solid #e1e7df;background:none;border-radius:3px;font-size:11px!important;white-space:nowrap;color:#87938a!important}.loop-button[aria-pressed=true]{color:#315c41!important;background:#edf5ee;border-color:#e0ece1}
.chapters{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;padding:0 22px 18px}.chapters button{text-align:left;min-width:0;border:0;border-top:2px solid #e9eee7;background:none;padding:10px 0 0;display:flex;gap:8px;align-items:center;font-size:11px!important;white-space:nowrap;color:#859187!important}.chapters button span{font-size:9px;font-variant-numeric:tabular-nums;opacity:.65}.chapters button[aria-pressed=true]{border-color:#447a54;color:#254235!important}
.geometry-inspector{border-top:1px solid #e8ede6;background:#f8faf7;padding:14px 22px;display:flex;gap:16px 22px;flex-wrap:wrap;align-items:center}.guide-switches{display:flex;gap:18px;align-items:center;flex-wrap:wrap}.guide-switches label{display:flex;gap:5px;align-items:center;font-size:11px;cursor:pointer}.guide-switches input{accent-color:#346b4b;margin:0 3px 0 0}.guide-switches span{color:#8c998e;font-size:10px}.circle-measurement{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:11px}.circle-measurement select{max-width:140px;border:1px solid #dce5d9;background:#fff;padding:5px 8px;border-radius:2px;font-size:11px}.circle-measurement output{font-variant-numeric:tabular-nums}.circle-measurement output span{margin-left:12px;color:#7a8a7e}.measurement-hint{color:#8a988c}
.small-previews{border-top:1px solid #e8ede6;display:flex;align-items:center;justify-content:flex-end;gap:32px;padding:16px 26px;min-height:109px}.preview-label{margin-right:auto;font-size:11px;color:#87928a;align-self:flex-start;padding-top:4px}.size-preview{display:flex;flex-direction:column;align-items:center;gap:9px;min-width:32px}.size-preview>span{font-size:9px;color:#8b978d;font-variant-numeric:tabular-nums}.size-art{display:flex;align-items:center;justify-content:center;flex:none;overflow:hidden}.size-art :deep(.logo-art){width:100%;height:100%;max-width:none;max-height:none;display:block}
.sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:650px){.studio-topbar{padding:0 15px;height:50px;gap:10px}.studio-views{gap:18px}.studio-views button{font-size:12px}.inspection-toggle{font-size:11px!important}.studio-surface{height:245px;padding:14px 8px 32px}.canvas-caption{left:16px;right:16px;font-size:8px}.transport{padding:12px 14px;gap:8px;flex-wrap:wrap}.play-button{width:28px;height:28px}.time-label{min-width:60px;font-size:10px}.loop-button{font-size:10px!important}.chapters{gap:7px;padding:0 14px 15px}.chapters button{display:flex;flex-direction:column;align-items:flex-start;gap:3px;font-size:10px!important}.chapters button span{font-size:8px}.small-previews{gap:24px;padding:16px;min-height:103px}.preview-label{font-size:10px}.geometry-inspector{padding:12px 15px}.circle-measurement output span{display:block;margin-left:0}.guide-switches{gap:15px}}
@media(prefers-reduced-motion:reduce){.studio-surface{transition:none}}
.construction-studio{border:0;border-radius:0;background:transparent;color:#21372c}
.studio-topbar{height:43px;padding:0 0 12px;border:0;gap:12px}
.studio-views{gap:25px}.studio-views button{font-size:10px;border-bottom:0;padding:0;color:#8b9983}.studio-views button[aria-pressed=true]{color:#2d5733;font-weight:600}
.inspection-toggle{font-size:9px!important;color:#7f9174!important}
.studio-surface{height:clamp(315px,30vw,470px);padding:35px 46px 50px;border-radius:3px 3px 90px 3px}
.studio-surface:not(.is-final){border:1px solid #dfe7d9;border-radius:3px}
.canvas-caption{left:25px;right:48px;bottom:22px;font-size:8px;letter-spacing:.07em;color:#789a70}
.is-final .canvas-caption{color:#e5eee0}.busy-label{top:14px;right:23px;font-size:9px;background:#f2f8edcc}
.process-controls{border:1px solid #e1e8dc;border-top:0;background:transparent}.transport{padding:13px 16px}.chapters{padding:0 16px 16px}
.small-previews{background:none;border:0;padding:18px 2px;min-height:88px;gap:25px}
@media(max-width:1000px){.studio-surface{padding:28px 27px 48px;border-bottom-right-radius:65px}.studio-views{gap:17px}.canvas-caption{font-size:7px}}
@media(max-width:650px){.studio-surface{height:280px;padding:24px 18px 43px;border-bottom-right-radius:55px}.studio-topbar{height:38px;padding:0 0 11px}.studio-views{gap:21px}.studio-views button{font-size:10px}.canvas-caption{left:17px;right:38px;bottom:18px;font-size:7px}.lettering-previews .size-preview:nth-child(2){display:none}.lettering-previews{gap:20px}.preview-label{font-size:9px}.inspection-toggle{font-size:9px!important}}
.studio-views button{font-size:12px;color:#788b70}.inspection-toggle{font-size:11px!important}.canvas-caption{font-size:9px}
@media(max-width:650px){.studio-views button{font-size:11px}.inspection-toggle{font-size:10px!important}.studio-views{gap:17px}.canvas-caption{font-size:8px}}
</style>
