# Changelog

## [0.5.0] - 2026-08-15

### Added

- Reviewed external per-scene WAV import from silent `xhs-video/v1` into voiced `xhs-video/v2`, with source/narration/audio hash binding, cue timing and transactional directory replacement.
- Deterministic rendering and media probing for one H.264 video stream plus one AAC 48 kHz mono audio stream.
- `xhs-video/v2` support in the platform publish checker, including derived synthetic-audio disclosure facts and on-disk WAV verification.

### Changed

- Voiceover remains bring-your-own audio: the project does not provide TTS, call a speech API or read an API key.
- Audio review and rights are two explicit user attestations; basic PCM activity is not audibility verification, and the tool does not verify licenses.
- Synthetic voiceover carries the fixed first-frame disclosure “旁白由AI合成”. A generated test tone is pipeline test data, not a real voiceover or release asset.
- Platform checks preserve the three-state `pass` / `needs_review` / `blocked` result; manual audio and rights items remain `needs_review` even when structural checks pass.

## [0.4.0] - 2026-08-15

### Added

- Versioned `xhs-video/v1` intermediate representation shared by short-video Markdown, Remotion Studio and MP4 rendering.
- Optional transactional 1080×1920, 30 fps, 60–90 second silent H.264 MP4 export using a local Chromium, Edge or Chrome executable.
- Same-scene visual, narration and first-caption warnings whenever a scene references unverified unsafe advice.

### Changed

- Video tooling keeps narration as text only (`audio.kind=none`); TTS, voice synthesis and browser downloads remain outside the project boundary.
- Failed MP4 renders preserve the previous complete output instead of replacing it with a partial file.
- Third-party licensing is now documented separately, including Remotion's special license.

## [0.3.0] - 2026-08-15

### Added

- Versioned `xhs-card-deck/v1` intermediate representation shared by Markdown and image renderers.
- Self-contained 1080×1440 HTML card decks, six visual themes and optional Playwright PNG capture.
- Paginated evidence-appendix PNG cards so published carousels can expose the canonical comment excerpts they reference.
- Browser-measured overflow failure, transactional whole-deck replacement, HTML escaping and deterministic output naming.

### Changed

- Card count now follows `7 + solution steps`, so every action keeps its evidence, applicability, verification and stop conditions on one card.
- Full evidence excerpts live in a separate appendix instead of overloading the disclosure card.
- Synthetic/high-risk disclosure moves to the cover; reader-facing metadata, single-case wording and the final safety question are publication-oriented.
- Unsafe excerpts carry a standalone warning in the appendix, and missing like counts are shown as unknown instead of silently rendered blank.

## [0.2.0] - 2026-08-15

### Added

- Claim ledger, evidence-quality labels, risk flags and independent reply-thread IDs.
- Capture coverage metadata and deterministic anonymization for exported comments.
- Strict validation for complete classification, eligible step evidence and high-risk publishing.
- Deterministic report, Xiaohongshu card and short-video Markdown renderers.
- Cross-platform CI with real installer checks.

### Changed

- Reader outputs now lead with the answer and expose applicability, verification, stop conditions, conflicts and sample truncation.
- Personal installation keeps one core Agent Skill and a thin Claude Code wrapper to prevent drift.

## [0.1.0] - 2026-08-09

### Added

- Cross-agent Skill entry points for Codex and Claude Code.
- Question-post classification and comment evidence rubric.
- JSON/JSONL normalization and evidence-reference validation.
- Safe personal installers, synthetic examples, and a vertical demo-video script.
