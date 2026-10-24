import type { DesignInput } from "../engine/types";
export const DRAFT_KEY = "leanschool:design:v3";
export type Draft = {
  version: 3;
  input: DesignInput;
  selectedId: string;
  ink: string;
  background: string;
  guides: boolean;
};
export const DEFAULT_INPUT: DesignInput = {
  text: "LeanSchool",
  weight: 24,
  spacing: 0,
  overlap: 44,
  tension: 52,
  seed: 0,
  mode: "geometry",
  composition: "auto",
  markText: undefined,
  focusIndex: undefined,
};
export function parseDraft(raw: string | null): Draft | null {
  if (!raw || raw.length > 4096) return null;
  try {
    const data = JSON.parse(raw) as Draft;
    const input = data.input;
    if (
      !data ||
      data.version !== 3 ||
      !input ||
      typeof input.text !== "string" ||
      !/^[a-z]{1,32}$/i.test(input.text) ||
      input.mode !== "geometry"
    )
      return null;
    for (const [key, min, max] of [
      ["weight", 14, 38],
      ["spacing", -20, 80],
      ["overlap", 24, 68],
      ["tension", 0, 100],
      ["seed", 0, 1000000],
    ] as const) {
      if (
        typeof input[key] !== "number" ||
        !Number.isFinite(input[key]) ||
        input[key] < min ||
        input[key] > max
      )
        return null;
    }
    if (
      !Number.isSafeInteger(input.seed) ||
      typeof data.ink !== "string" ||
      typeof data.background !== "string" ||
      !/^#[\da-f]{6}$/i.test(data.ink) ||
      !/^#[\da-f]{6}$/i.test(data.background) ||
      typeof data.guides !== "boolean" ||
      typeof data.selectedId !== "string" ||
      !/^[a-z]{1,20}$/.test(data.selectedId)
    )
      return null;
    if (input.composition !== undefined && !['auto', 'monogram', 'wordmark', 'signature'].includes(input.composition)) return null;
    if (input.markText !== undefined && (typeof input.markText !== 'string' || (input.markText !== '' && !/^[a-z]{1,4}$/i.test(input.markText)))) return null;
    if (input.focusIndex !== undefined && (!Number.isInteger(input.focusIndex) || input.focusIndex < 0 || input.focusIndex >= input.text.length)) return null;
    return {
      version: 3,
      input: {
        text: input.text,
        mode: input.mode,
        weight: input.weight,
        spacing: input.spacing,
        overlap: input.overlap,
        tension: input.tension,
        seed: input.seed,
        composition: input.composition || 'auto',
        markText: input.markText || undefined,
        focusIndex: input.focusIndex,
      },
      ink: data.ink,
      background: data.background,
      guides: data.guides,
      selectedId: data.selectedId,
    };
  } catch {
    return null;
  }
}
