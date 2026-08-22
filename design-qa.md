# Design QA — Flat Workbench Refresh

## Comparison target

- Source visual truth: `/Users/feng/.codex/attachments/4f222bf7-3343-44d7-a126-504c7db0ebdd/pasted-text.txt`
- Rendered implementation: `http://127.0.0.1:5173/`
- Implementation screenshot: `/Users/feng/.codex/visualizations/2026/08/22/01a02744-31e6-7283-a3c2-e4113507934e/flat-ui-desktop.png`
- State: 当前工作台，项目“第一个项目”，已完成 Run，Test AUC 0.6407、Test KS 0.2749
- Viewport: CSS `1440 × 900`; implementation screenshot `1440 × 900` pixels; browser capture density `1x`; no source screenshot available for normalization

## Evidence

- Full-view implementation capture shows the reference-led visual language: `#f3f4f6` canvas, white translucent rounded surfaces, confident blue hero/action surfaces, black status blocks, compact mono labels, and soft elevation.
- Focused regions checked in the rendered implementation: left project rail, blue run summary, metric strip, candidate table, right stage rail, bottom Agent chat, and primary tab strip. A focused source-vs-implementation crop comparison was not completed because the attached HTML file could not be opened as a visual page in the approved browser surface.
- Primary interactions tested: `当前工作台` → `产物报告` → `历史 Run` → `当前工作台`; real report metrics and Run history remained visible.
- Browser console check: 0 error/warning entries observed.

## Required fidelity surfaces

- Fonts and typography: the implementation uses the reference-inspired `Plus Jakarta Sans` / `Inter` / Chinese system fallback chain plus the existing mono label treatment. Exact font rendering against the source remains unverified without a source capture.
- Spacing and layout rhythm: rounded 20px outer surfaces, 14px grid gaps, 16–26px content padding, blue hero block, and fixed one-screen desktop shell were verified in the rendered implementation.
- Colors and visual tokens: the implementation maps the reference palette to `--ground`, `--blue`, `--black`, `--panel`, `--line`, and semantic status tokens in `frontend/src/styles.css`.
- Image quality and asset fidelity: the provided reference is CSS/UI-led and does not require new imagery; no new decorative image or SVG approximation was introduced.
- Copy and content: existing risk-model copy and real metrics were preserved; only presentation styling changed.

## Findings

- [P1] Source visual capture is unavailable.
  Location: QA evidence, source attachment.
  Evidence: the source is available as HTML text, but the approved browser surface rejected the `file://` URL, so a visual source screenshot could not be opened and placed beside the implementation capture.
  Impact: exact pixel-level comparison of typography, spacing, and component proportions cannot be claimed.
  Fix: attach a screenshot/mockup of the reference or provide it through an approved local preview route, then rerun this QA report at the same viewport.

## Comparison history

1. Initial implementation: the persistent Agent chat was pushed below the desktop viewport (`top ≈ 1131`, viewport bottom `1125`). Fixed by constraining the app shell/main column and keeping the workspace internally scrollable. Post-fix evidence: Agent chat visible at `top ≈ 921` with the workspace scrolling inside its frame.
2. Responsive pass: the narrow layout allowed the tab strip to increase the main column beyond the viewport. Fixed with a constrained mobile grid column, `min-width: 0`, and tab-strip overflow. Post-fix evidence at CSS `390 × 844`: root/body width `390`, no horizontal overflow, and Agent chat remained present.
3. Source-capture blocker remains open; no further visual fix should be inferred without the source capture.

## Implementation checklist

- [x] Match the reference-led light canvas, rounded panels, blue/black emphasis, and soft shadows.
- [x] Keep existing data, metrics, tabs, report, history, and Agent chat behavior intact.
- [x] Verify desktop one-screen composition and narrow-screen overflow behavior.
- [x] Check browser console errors/warnings and primary tab interactions.
- [ ] Re-run side-by-side source comparison after an approved visual source capture is available.

## Follow-up polish

- Confirm the exact display font used by the source and decide whether it should be bundled locally instead of relying on the fallback chain.

final result: blocked
