<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId } from 'vue';
import { capPolygon, circleStyle, measureReferenceTangency, parseReferenceGeometry,
  phaseContains, polygonClip, referenceCircles, type ReferenceGeometry,
  type ReferenceMode, type ReferencePhase } from '../lib/reference-lab';

// Self-contained fixed SR study: intentionally has no current-wordmark props.
const uid = `reference-${useId().replace(/:/g, '')}`;
const resourceBase = `${import.meta.env.BASE_URL}reference/`;
const imageUrl = `${resourceBase}sr-original.png`;
const geometry = ref<ReferenceGeometry>();
const error = ref(''), loading = ref(false), referenceFailed = ref(false);
const phase = ref<ReferencePhase>('final'), mode = ref<ReferenceMode>('plain');
const opacity = ref(50), selectedId = ref(0);
const guides = ref(false), jointsVisible = ref(false), dark = ref(false);
const controller = new AbortController();
const circles = computed(() => geometry.value ? referenceCircles(geometry.value) : []);
const lines = computed(() => geometry.value?.parts.flatMap(part => part.arcs.filter(arc => !arc.circle)) ?? []);
const tangency = computed(() => geometry.value ? measureReferenceTangency(geometry.value) : undefined);
const selected = computed(() => circles.value[selectedId.value]);
const parts = computed(() => geometry.value?.parts.map(part => ({
  ...part,
  baseClip: polygonClip([part.start, ...part.arcs.map(arc => arc.to)], geometry.value!),
  // Preserve upstream paint order per part: base, all additions, all cuts.
  caps: [true, false].flatMap(add => part.arcs.filter(arc => arc.circle && arc.add === add).map(arc => ({
    ...arc, clip: polygonClip(capPolygon(arc, geometry.value!), geometry.value!),
    disk: circleStyle(arc.circle!, geometry.value!),
  }))),
})) ?? []);
const views = [{ id: 'main', size: 0 }, { id: '96', size: 96 }, { id: '48', size: 48 }, { id: '32', size: 32 }];
const phases = [{ id: 'base', title: '直边底形' }, { id: 'add', title: '外圆填色' }, { id: 'final', title: '内圆切除' }] as const;
const modes = [{ id: 'plain', title: '纯色结果' }, { id: 'overlay', title: '原图叠加' }, { id: 'diff', title: '差异检查' }] as const;
const phaseTitle = computed(() => phases.find(item => item.id === phase.value)!.title);
const help = computed(() => ({
  plain: '固定 SR 的几何校正版。直边与圆形使用同一组坐标，局部轮廓允许偏离原图。',
  overlay: '两层共用坐标与裁切：0% 为几何结果，100% 为原图，可检查错位和接点。',
  diff: '一致区域接近黑色，错位边缘变亮。原图的虚线、纹理和明暗差也会显示；辅助线暂时隐藏。',
})[mode.value]);
const number = (value: number) => Number(value.toFixed(2)).toString();
const tangentError = computed(() => {
  const value = tangency.value?.maxError;
  if (value === undefined || value === null || !tangency.value?.complete) return '';
  return value < .000001 ? '< 0.000001°' : `${value.toFixed(6)}°`;
});
function chooseCircle() { guides.value = true; }
function imageFailed() { referenceFailed.value = true; mode.value = 'plain'; }
async function loadGeometry() {
  loading.value = true; error.value = '';
  try {
    const response = await fetch(`${resourceBase}sr-geometry.json`, { signal: controller.signal });
    if (!response.ok) throw new Error('SR 原型资源暂时无法加载');
    geometry.value = parseReferenceGeometry(await response.json());
  } catch (reason) {
    if (!controller.signal.aborted) error.value = reason instanceof Error ? reason.message : 'SR 原型资源暂时无法加载';
  } finally { loading.value = false; }
}
onMounted(loadGeometry);
onBeforeUnmount(() => controller.abort());
</script>

<template>
  <section class="reference-lab" :aria-labelledby="`${uid}-title`">
    <header class="lab-heading">
      <div><h3 :id="`${uid}-title`">SR 原型研究</h3><p>固定原图与几何对照，不随上方字母组合变化。</p></div>
      <a :href="imageUrl" target="_blank" rel="noopener">查看完整原图 ↗</a>
    </header>
    <p v-if="loading" class="lab-status" role="status">正在加载 SR 原型…</p>
    <div v-else-if="error" class="lab-status" role="alert">{{ error }} <button type="button" @click="loadGeometry">重试</button></div>
    <template v-else-if="geometry">
      <div class="lab-steps" role="group" aria-label="SR 实体构造步骤">
        <button v-for="(step, index) in phases" :key="step.id" type="button" :aria-pressed="phase === step.id" @click="phase = step.id"><span>0{{ index + 1 }}</span>{{ step.title }}</button>
      </div>
      <div class="lab-controls">
        <div class="lab-modes" role="group" aria-label="SR 原图对比方式">
          <button v-for="option in modes" :key="option.id" type="button" :aria-pressed="mode === option.id" :disabled="referenceFailed && option.id !== 'plain'" @click="mode = option.id">{{ option.title }}</button>
        </div>
        <label class="lab-opacity" :for="`${uid}-opacity`">原图透明度 <input :id="`${uid}-opacity`" v-model.number="opacity" type="range" min="0" max="100" step="1" :disabled="mode !== 'overlay'" :aria-valuetext="`${opacity}%`" /><output :for="`${uid}-opacity`">{{ opacity }}%</output></label>
      </div>
      <p v-if="referenceFailed" class="lab-image-error" role="alert">原图未能加载，几何研究仍可使用。</p>
      <div class="lab-panels">
        <article class="lab-panel original-panel">
          <h4>原图 · SR 局部</h4>
          <div class="lab-canvas"><div class="lab-reference"><img :src="imageUrl" alt="原始设计图的 SR 标志局部" @error="imageFailed" /></div></div>
        </article>
        <article v-for="view in views" :key="view.id" class="lab-panel" :class="view.size ? 'small-panel' : 'geometry-panel'">
          <h4>{{ view.size ? `${view.size} px · 最终结果` : phaseTitle }}<span v-if="!view.size">{{ circles.length }} 圆 / {{ lines.length }} 直边</span></h4>
          <div class="lab-canvas" :class="{ 'is-dark': dark && (view.size || mode === 'plain') }">
            <div class="lab-drawing" :class="{ 'is-diff': !view.size && mode === 'diff' }" :style="view.size ? { width: `${view.size}px` } : undefined">
              <div class="lab-mark" role="img" :aria-label="`SR 几何原型 · ${view.size ? '最终结果' : phaseTitle}`">
                <template v-for="(part, partIndex) in parts" :key="partIndex">
                  <div class="lab-base" :style="{ clipPath: part.baseClip }"></div>
                  <template v-for="(cap, capIndex) in part.caps" :key="`${partIndex}-${capIndex}`">
                    <div v-if="phaseContains(view.size ? 'final' : phase, cap)" class="lab-cap" :class="{ 'is-cut': !cap.add }" :style="{ clipPath: cap.clip }"><i class="lab-disc" :style="cap.disk"></i></div>
                  </template>
                </template>
                <svg v-if="!view.size && mode !== 'diff' && (guides || jointsVisible)" class="lab-guides" :viewBox="`0 0 ${geometry.width} ${geometry.height}`" aria-hidden="true">
                  <g v-if="guides" fill="none">
                    <circle v-for="circle in circles" :key="circle.id" :cx="circle.circle[0]" :cy="circle.circle[1]" :r="circle.circle[2]" :stroke="circle.add ? '#168570' : '#b95132'" :stroke-width="circle.id === selectedId ? 1.5 : .8" :stroke-dasharray="circle.id === selectedId ? undefined : '4 3'" :opacity="circle.id === selectedId ? 1 : .5" />
                    <g v-for="(line, index) in lines" :key="index" stroke="#497da6" stroke-width="1"><line :x1="line.from[0]" :y1="line.from[1]" :x2="line.to[0]" :y2="line.to[1]" /><circle v-for="(endpoint, n) in [line.from, line.to]" :key="n" :cx="endpoint[0]" :cy="endpoint[1]" r="2" fill="#497da6" stroke="none" /></g>
                    <line v-if="selected" :x1="selected.from[0]" :y1="selected.from[1]" :x2="selected.to[0]" :y2="selected.to[1]" stroke="#b95132" stroke-width="1" />
                  </g>
                  <g v-if="jointsVisible" stroke="#168570" stroke-width="1.2">
                    <g v-for="(joint, index) in tangency?.joints ?? []" :key="index"><line :x1="joint.point[0] - joint.tangent[0] * 12" :y1="joint.point[1] - joint.tangent[1] * 12" :x2="joint.point[0] + joint.tangent[0] * 12" :y2="joint.point[1] + joint.tangent[1] * 12" /><circle :cx="joint.point[0]" :cy="joint.point[1]" r="3" fill="var(--lab-paper)" /></g>
                  </g>
                </svg>
              </div>
              <div v-if="!view.size && mode !== 'plain' && !referenceFailed" class="lab-reference lab-overlay" :style="{ opacity: mode === 'diff' ? 1 : opacity / 100 }" aria-hidden="true"><img :src="imageUrl" alt="" @error="imageFailed" /></div>
            </div>
          </div>
        </article>
      </div>
      <p class="lab-help" aria-live="polite">{{ help }}</p>
      <div class="lab-tools" role="group" aria-label="SR 几何辅助视图">
        <button type="button" :aria-pressed="guides" @click="guides = !guides">圆与直线</button>
        <button type="button" :aria-pressed="jointsVisible" @click="jointsVisible = !jointsVisible">相切接点</button>
        <button type="button" :aria-pressed="dark" :disabled="mode !== 'plain'" @click="dark = !dark">深色背景</button>
      </div>
      <div class="lab-measurement">
        <div>
          <label :for="`${uid}-circle`">选择参与轮廓的圆</label>
          <select :id="`${uid}-circle`" v-model.number="selectedId" @change="chooseCircle"><option v-for="circle in circles" :key="circle.id" :value="circle.id">{{ circle.id + 1 }} · {{ circle.name }} / {{ circle.add ? '外圆补色' : '内圆切除' }}</option></select>
          <p v-if="selected" class="lab-measure" aria-live="polite">圆心 ({{ number(selected.circle[0]) }}, {{ number(selected.circle[1]) }}) · 半径 {{ number(selected.circle[2]) }} · {{ selected.add ? '补出外凸弧' : '切出内凹弧' }}</p>
        </div>
        <div class="lab-tangency"><span>几何衔接</span><p v-if="tangentError">{{ tangency!.joints.length }} 个指定平滑接点 · 实算最大切线误差 {{ tangentError }}</p><p v-else>接点测量未完整取得。</p><small>仅测量原型中指定的平滑接点；笔画尖端与直线转角保留。</small></div>
      </div>
      <div class="lab-legend"><span><i class="add-key"></i>绿色：外圆补色</span><span><i class="cut-key"></i>橙色：内圆切除</span><span><i class="line-key"></i>蓝色：直边</span></div>
    </template>
  </section>
</template>

<style scoped>
.reference-lab{--lab-paper:#fff;--lab-ink:#202020;color:#254235;font:13px/1.6 Arial,"PingFang SC","Microsoft YaHei",sans-serif;min-width:0}
.reference-lab *{box-sizing:border-box}.reference-lab h3,.reference-lab h4,.reference-lab p{margin:0}.reference-lab button,.reference-lab select{font:inherit;color:inherit;background:#fff;border:1px solid #dce5d9;border-radius:3px;padding:7px 11px}.reference-lab button{cursor:pointer}.reference-lab button[aria-pressed=true]{color:#fff;background:#254235;border-color:#254235}.reference-lab button:disabled,.reference-lab input:disabled{opacity:.45;cursor:default}.reference-lab :is(button,select,input,a):focus-visible{outline:2px solid #447a54;outline-offset:3px}.reference-lab a{color:#315c41;text-decoration:underline;text-underline-offset:3px}
.lab-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:20px}.lab-heading h3{font-size:19px;font-weight:600}.lab-heading p,.lab-heading a{font-size:12px}.lab-heading p{color:#738276;margin-top:4px}.lab-heading a{white-space:nowrap}.lab-status{padding:24px 0}.lab-status button{margin-left:12px}.lab-steps,.lab-modes,.lab-tools{display:flex;flex-wrap:wrap;gap:8px}.lab-steps{margin-bottom:14px}.lab-steps span{margin-right:10px;font-size:10px;opacity:.65}.lab-controls{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px}.lab-opacity{display:flex;align-items:center;gap:10px;font-size:12px;white-space:nowrap}.lab-opacity input{width:120px;accent-color:#356b4c;margin:0}.lab-opacity output{min-width:34px;font-variant-numeric:tabular-nums}.lab-image-error{color:#9d3d29;margin-bottom:10px!important}
.lab-panels{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.lab-panel{grid-column:span 3;min-width:0;border:1px solid #dce5d9;border-radius:3px;overflow:hidden;background:#fff}.lab-panel h4{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid #e8ede6;font-size:12px;font-weight:500}.lab-panel h4 span{font-size:10px;color:#7e8b81;white-space:nowrap}.lab-canvas{display:grid;place-items:center;padding:26px;background:var(--lab-paper)}.lab-canvas.is-dark{--lab-paper:#202328;--lab-ink:#f4f4f4}.lab-reference,.lab-drawing{width:100%;max-width:420px;aspect-ratio:520/505}.lab-drawing{position:relative;isolation:isolate}.lab-reference{position:relative;overflow:hidden;background:#fff}.lab-reference img{position:absolute;width:241.153846%;max-width:none;height:auto;left:-74.038462%;top:-23.762376%}.lab-mark{position:relative;width:100%;aspect-ratio:520/505;isolation:isolate;background:var(--lab-paper)}.lab-base,.lab-cap,.lab-guides{position:absolute;inset:0}.lab-base,.lab-disc{background:var(--lab-ink)}.lab-cap{pointer-events:none}.lab-disc{position:absolute;display:block;border-radius:50%;aspect-ratio:1}.lab-cap.is-cut .lab-disc{background:var(--lab-paper)}.lab-guides{z-index:5;width:100%;height:100%;overflow:hidden;pointer-events:none}.lab-overlay{position:absolute;inset:0;z-index:6;pointer-events:none}.is-diff .lab-overlay{mix-blend-mode:difference}.small-panel{grid-column:span 2}.small-panel .lab-canvas{min-height:145px;padding:18px}.small-panel h4{font-size:11px}.lab-help{font-size:12px;color:#738276;margin:12px 0 16px!important}.lab-tools{margin-bottom:16px}.lab-tools button{font-size:12px}
.lab-measurement{display:grid;grid-template-columns:1fr 1fr;gap:24px;padding:18px 0;border-top:1px solid #e8ede6}.lab-measurement label,.lab-tangency>span{display:block;font-size:12px;margin-bottom:8px;color:#738276}.lab-measurement select{width:100%;max-width:380px}.lab-measure{margin-top:8px!important;font-size:11px;font-variant-numeric:tabular-nums}.lab-tangency p{font-size:12px}.lab-tangency small{display:block;font-size:11px;color:#7e8b81;margin-top:6px}.lab-legend{display:flex;flex-wrap:wrap;gap:20px;font-size:11px;color:#738276}.lab-legend i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}.add-key{background:#168570}.cut-key{background:#b95132}.line-key{background:#497da6}
@media(max-width:650px){.lab-heading{display:block}.lab-heading a{display:inline-block;margin-top:8px}.lab-steps{gap:6px}.lab-steps button{flex:1;padding:8px 5px;font-size:11px}.lab-steps span{margin-right:5px}.lab-controls{gap:12px}.lab-modes button{font-size:11px;padding:7px 10px}.lab-panel{grid-column:span 6}.lab-panel h4{padding:11px 13px}.lab-canvas{padding:22px}.small-panel{grid-column:span 2}.small-panel .lab-canvas{padding:8px;min-height:122px}.small-panel h4{padding:8px;font-size:9px}.lab-measurement{grid-template-columns:1fr;gap:18px}.lab-legend{gap:12px}}
@media(max-width:420px){.small-panel{grid-column:span 6;display:flex;align-items:center;justify-content:space-between}.small-panel h4{padding:12px;border-bottom:0;font-size:11px}.small-panel .lab-canvas{width:128px;min-height:112px;flex:none}}
</style>
