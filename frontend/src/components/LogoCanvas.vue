<script setup lang="ts">
import type { DesignVariant } from "../engine/types";
import { focusViewBox, focusContentBox } from '../lib/focus-preview';
import { useId } from 'vue';
const clipId = 'focus-' + useId().replace(/:/g, '');
withDefaults(
  defineProps<{
    variant?: DesignVariant;
    color: string;
    guides?: boolean;
    progress?: number;
    thumbnail?: boolean;
    detail?: boolean;
  }>(),
  { progress: 1 },
);
</script>
<template>
  <svg
    v-if="variant"
    class="logo-art"
    :class="{ 'logo-art--thumbnail': thumbnail, 'logo-art--detail': detail }"
    :viewBox="(detail ? focusViewBox(variant) : variant.viewBox).join(' ')"
    :aria-label="`${variant.text} · ${variant.name}`"
    role="img"
    :style="{ color }"
  >
    <defs v-if="detail"><clipPath :id="clipId"><rect v-bind="focusContentBox(variant)" /></clipPath></defs>
    <g
      v-if="guides && !thumbnail"
      class="construction"
      fill="none"
      stroke="currentColor"
      stroke-width=".45"
      opacity=".32"
    >
      <path v-for="(d, i) in variant.guides.paths" :key="`g${i}`" :d="d" />
      <line
        v-for="(line, i) in variant.guides.lines || []"
        :key="`l${i}`"
        :x1="line[0]"
        :y1="line[1]"
        :x2="line[2]"
        :y2="line[3]"
        stroke-dasharray="2 3"
      />
      <circle
        v-for="(circle, i) in variant.guides.circles || []"
        :key="`c${i}`"
        :cx="circle[0]"
        :cy="circle[1]"
        :r="circle[2]"
      />
    </g>
    <g fill="currentColor" :fill-rule="variant.fillRule" :clip-path="detail ? `url(#${clipId})` : undefined">
      <path v-for="(d, i) in variant.paths" :key="i" :d="d" />
    </g>
  </svg>
</template>
