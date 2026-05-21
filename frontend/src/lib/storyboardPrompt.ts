// Locked prompt template for Storyboard nodes. The node IS an image
// node — it just wraps the user's topic in a deterministic preamble
// so Flow renders a single composite grid that visually narrates the
// topic. Tweak wording here, never inline at the dispatch site.
import type { StoryboardGrid } from "../store/board";

export const STORYBOARD_GRIDS: readonly StoryboardGrid[] = [
  "2x2",
  "2x3",
  "2x4",
] as const;

// Normalise whatever was persisted on a node (incl. legacy "3x3" from
// 1.2.15-1.2.18) into a valid grid. Unknown / legacy → "2x2" (the
// simplest baseline; user can re-pick).
export function normaliseStoryboardGrid(
  value: unknown,
  fallback: StoryboardGrid = "2x2",
): StoryboardGrid {
  return value === "2x2" || value === "2x3" || value === "2x4"
    ? value
    : fallback;
}

export function totalPanels(grid: StoryboardGrid): number {
  return grid === "2x2" ? 4 : grid === "2x3" ? 6 : 8;
}

// Map grid + image aspect ratio → concrete rows × cols. For asymmetric
// grids (2x3, 2x4), the larger dimension follows the longer edge of
// the image so panels remain readable: landscape → wider grid (cols
// = larger), portrait → taller grid (rows = larger). 2x2 is symmetric.
export function resolveStoryboardLayout(
  grid: StoryboardGrid,
  aspectRatio?: string,
): { rows: number; cols: number; total: number } {
  if (grid === "2x2") return { rows: 2, cols: 2, total: 4 };
  const big = grid === "2x3" ? 3 : 4; // total = 6 or 8
  // Flow's aspect-ratio enums look like IMAGE_ASPECT_RATIO_PORTRAIT
  // or VIDEO_ASPECT_RATIO_PORTRAIT — substring match catches both.
  const isPortrait = aspectRatio?.includes("PORTRAIT") ?? false;
  return isPortrait
    ? { rows: big, cols: 2, total: 2 * big }
    : { rows: 2, cols: big, total: 2 * big };
}

export function buildStoryboardPrompt(
  topic: string,
  grid: StoryboardGrid = "2x2",
  aspectRatio?: string,
): string {
  const { rows, cols, total } = resolveStoryboardLayout(grid, aspectRatio);
  const t = topic.trim() || "untitled story";
  // Verbose template — earlier short version produced overlapping borders
  // (no clear panel separators) and no per-frame captions, so the result
  // read like a montage instead of a storyboard. This version pins the
  // layout, numbering, and caption rules so each panel is self-explanatory
  // at a glance.
  //
  // Intentionally STYLE-NEUTRAL — no medium hints ("comic-book", "manga",
  // "comic art"), no font hints ("sans-serif"), no palette / cohesion
  // clauses. Those override the upstream refs (character, visual_asset)
  // that should drive the visual look, and add noise when no refs are
  // attached. Only layout / numbering / caption rules remain — those are
  // the actual non-negotiables for a readable storyboard.
  return [
    `Create a visual storyboard for "${t}" as a SINGLE IMAGE`,
    `arranged in a ${rows}x${cols} grid (${rows} rows, ${cols} columns, ${total} panels total).`,
    `Each panel illustrates one beat of the story.`,
    `Panels read left-to-right, top-to-bottom in narrative order (1 → ${total}).`,
    `STRICT layout rules:`,
    `  • Clean WHITE GUTTERS between every panel — no overlapping borders, no bleed between scenes.`,
    `  • Each panel is rectangular, identical size, sharply separated from its neighbors.`,
    `  • In the TOP-LEFT corner of every panel, place a small filled CIRCLE with the panel NUMBER (1, 2, 3, …, ${total}) inside it — readable and consistent across all panels.`,
    `  • BENEATH each panel (outside the picture area, in the white gutter), print a SHORT one-sentence CAPTION describing the action of that beat. Use clean, legible text. Captions in the same language as the topic.`,
  ].join(" ");
}

// Locked motion prompt for video nodes whose upstream image is a
// Storyboard composite. Forces Flow to animate the panels in order
// (1 → N) rather than re-interpret the composite as one scene.
//   2x2 grid → 4 panels → "frame 1 to frame 4"
//   2x3 grid → 6 panels → "frame 1 to frame 6"
//   2x4 grid → 8 panels → "frame 1 to frame 8"
// Other refs (character / location / visual_asset) still flow into
// the video request alongside the storyboard source — the prompt
// itself is what's locked.
export function buildStoryboardVideoPrompt(
  grid: StoryboardGrid = "2x2",
): string {
  const lastFrame = totalPanels(grid);
  return `A 10-seconds cinematic animated film trailer following narrative progression from exactly frame 1 to frame ${lastFrame} of the image reference`;
}
