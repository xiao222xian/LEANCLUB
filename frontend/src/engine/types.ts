export type DesignMode = 'geometry';
export type DesignComposition = 'auto' | 'monogram' | 'wordmark' | 'signature';

export interface DesignInput {
  text: string;
  /** Original geometric stroke weight, 14–38. */
  weight: number;
  /** Layout spacing, -20–80. The original pair advances the second glyph by value × 0.4. */
  spacing: number;
  /** Original geometric overlap, 24–68. */
  overlap: number;
  /** Original tension, 0–100, controlling cuts and angled relationships. */
  tension: number;
  /** Reproducible composition variation. */
  seed: number;
  mode: DesignMode;
  composition?: DesignComposition;
  /** Optional signature symbol, 1–4 letters. The full name is always retained. */
  markText?: string;
  /** Optional full-name glyph index for a local edit; undefined selects automatically. */
  focusIndex?: number;
}

export interface DesignPlacement {
  character: string;
  index: number;
  /** Final, uncombined glyph path (preserves input case and all contour components). */
  path: string;
  bounds: [number, number, number, number];
}

export interface DesignVariant {
  id: string;
  name: string;
  text: string;
  mode: DesignMode;
  paths: string[];
  fillRule: 'evenodd' | 'nonzero';
  viewBox: [number, number, number, number];
  bounds: [number, number, number, number];
  guides: {
    paths: string[];
    lines: number[][];
    circles: number[][];
    joints: { point: number[]; tangent: number[] }[];
  };
  placements: DesignPlacement[];
  /** Geometric stroke weight for monograms; actual 300–700 font master for names. */
  resolvedWeight: number;
  statistics: { characters: number; groups: number; contours: number; arcs: number; segments: number };
  layout?: Exclude<DesignComposition, 'auto'>;
  description?: string;
  detail?: string;
  modifiedIndices?: number[];
  signature?: { markText: string; source: 'camelcase' | 'initials' | 'custom' };
}

export interface VariantOption { id: string; name: string }
