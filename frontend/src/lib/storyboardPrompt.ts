// Locked prompt template for Storyboard nodes. The node IS an image
// node — it just wraps the user's topic in a deterministic preamble
// so Flow renders a single composite grid that visually narrates the
// topic. Tweak wording here, never inline at the dispatch site.
import type { StoryboardGrid } from "../store/board";

export function buildStoryboardPrompt(
  topic: string,
  grid: StoryboardGrid = "3x3",
): string {
  const n = grid === "2x2" ? 2 : 3;
  const t = topic.trim() || "untitled story";
  return `Create visual storyboard for "${t}" as SINGLE IMAGE arranged in a ${n}x${n} layout (${n} rows, ${n} columns)`;
}
