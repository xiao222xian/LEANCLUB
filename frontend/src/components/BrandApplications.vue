<script setup lang="ts">
import { computed, ref } from 'vue';
import type { DesignVariant } from '../engine/types';
import { brandSheetDocument, exportBrandSheet, loadBrandPhoto, type BrandScene } from '../lib/brand-sheet';

const props = defineProps<{ variant: DesignVariant; color: string; background: string }>();
const scene = ref<BrandScene>('card');
const photoDataUrl = ref(''), error = ref(''), exporting = ref(false), loadingPhoto = ref(false);
let pendingPhoto: Promise<string> | undefined;
const sheet = computed(() => {
  if (scene.value === 'poster' && !photoDataUrl.value) return '';
  return brandSheetDocument(props.variant, { scene: scene.value, color: props.color, background: props.background, photoDataUrl: photoDataUrl.value }).svg;
});
async function preparePhoto() {
  if (photoDataUrl.value) return;
  loadingPhoto.value = true;
  try {
    pendingPhoto ||= loadBrandPhoto();
    photoDataUrl.value = await pendingPhoto;
  } catch (cause) {
    pendingPhoto = undefined;
    error.value = cause instanceof Error ? cause.message : '样张照片加载失败，请重试';
  } finally { loadingPhoto.value = false; }
}
async function selectScene(value: BrandScene) {
  scene.value = value; error.value = '';
  if (value === 'poster') await preparePhoto();
}
async function download(format: 'svg' | 'png') {
  if (exporting.value || !sheet.value) return;
  exporting.value = true; error.value = '';
  try {
    await exportBrandSheet(props.variant, { scene: scene.value, color: props.color, background: props.background, photoDataUrl: photoDataUrl.value, format });
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '样张导出失败，请重试'; }
  finally { exporting.value = false; }
}
</script>

<template>
  <section class="brand-applications" aria-label="品牌应用样张">
    <div class="application-tools">
      <div class="scene-switch" aria-label="应用场景">
        <button type="button" :aria-pressed="scene === 'card'" @click="selectScene('card')">品牌名片</button>
        <button type="button" :aria-pressed="scene === 'poster'" @click="selectScene('poster')">活动海报</button>
      </div>
      <div class="sheet-downloads" aria-label="下载当前样张">
        <button type="button" :disabled="exporting || !sheet" @click="download('svg')">SVG <span aria-hidden="true">↗</span></button>
        <button type="button" :disabled="exporting || !sheet" @click="download('png')">PNG <span aria-hidden="true">↗</span></button>
      </div>
    </div>
    <div class="application-stage" :class="scene" :aria-busy="loadingPhoto || exporting">
      <div v-if="sheet" class="sheet-artwork" role="img" :aria-label="`${variant.text} · ${scene === 'card' ? '品牌名片' : '薄肌活动海报'}应用样张`" v-html="sheet" />
      <span v-else class="photo-placeholder">{{ loadingPhoto ? '加载样张…' : '照片暂未加载' }}</span>
    </div>
    <div v-if="error" class="sheet-error" role="alert">{{ error }} <button v-if="scene === 'poster' && !photoDataUrl" type="button" @click="selectScene('poster')">重试</button></div>
  </section>
</template>

<style scoped>
.brand-applications { width: 100%; color: #21372c; }
.application-tools { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 2px 15px; }
.scene-switch, .sheet-downloads { display: flex; align-items: center; gap: 5px; }
button { appearance: none; border: 0; font: inherit; cursor: pointer; color: inherit; transition: background .18s, color .18s; }
button:focus-visible { outline: 2px solid #477a5f; outline-offset: 3px; }
.scene-switch { padding: 4px; background: #edf1ea; border-radius: 9px; }
.scene-switch button { font-size: 12px; font-weight: 600; padding: 8px 13px; border-radius: 6px; background: transparent; }
.scene-switch button[aria-pressed="true"] { background: #fff; box-shadow: 0 1px 4px #21372c0b; }
.sheet-downloads button { background: transparent; padding: 7px 9px; font-size: 11px; letter-spacing: .06em; }
.sheet-downloads button:hover { background: #e8eee7; border-radius: 5px; }
.sheet-downloads span { padding-left: 3px; }
.sheet-downloads button:disabled { opacity: .35; cursor: wait; }
.application-stage { height: 390px; padding: 30px; display: flex; align-items: center; justify-content: center; background: #e6e9e2; border: 1px solid #dce2d8; border-radius: 12px; overflow: hidden; }
.sheet-artwork { width: 100%; max-width: 560px; aspect-ratio: 12/7; box-shadow: 0 18px 30px -19px #21372c70; transform: rotate(-2deg); }
.sheet-artwork :deep(svg) { display: block; width: 100%; height: 100%; }
.poster .sheet-artwork { height: 340px; width: auto; max-width: 100%; aspect-ratio: 3/4; transform: none; }
.photo-placeholder { font-size: 12px; color: #6e7b70; }
.sheet-error { display: flex; align-items: center; gap: 10px; margin-top: 10px; font-size: 12px; color: #80523b; }
.sheet-error button { background: transparent; text-decoration: underline; padding: 4px; }
@media (max-width: 600px) {
  .application-tools { gap: 5px; padding-bottom: 12px; }
  .scene-switch button { padding: 7px 9px; font-size: 11px; }
  .sheet-downloads { gap: 0; }
  .sheet-downloads button { padding: 7px; font-size: 10px; }
  .application-stage { height: 340px; padding: 23px; border-radius: 9px; }
  .poster .sheet-artwork { height: 294px; }
}
@media (prefers-reduced-motion: reduce) { button { transition: none; } }
</style>
