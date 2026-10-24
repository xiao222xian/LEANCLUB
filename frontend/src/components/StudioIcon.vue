<script setup lang="ts">
defineProps<{ name: string; size?: number }>();
const paths: Record<string, string> = {
  arrow: "M5 19 19 5M5 5h14v14",
  download: "M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5",
  refresh:
    "M20 7v5h-5M4 17v-5h5M6.1 6.1A8 8 0 0 1 20 12M4 12a8 8 0 0 0 13.9 5.9",
  close: "m6 6 12 12M6 18 18 6",
  play: "m8 5 11 7-11 7Z",
  pause: "M8 5v14M16 5v14",
  check: "m5 12 4 4L19 6",
  grid: "M4 8h16M4 16h16M8 4v16M16 4v16",
  chevron: "m8 10 4 4 4-4",
  reset: "M4 5v6h6M4 11a8 8 0 1 1 1 6",
  expand: "M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5",
};
</script>
<template>
  <svg
    :width="size || 18"
    :height="size || 18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.arrow" />
  </svg>
</template>
