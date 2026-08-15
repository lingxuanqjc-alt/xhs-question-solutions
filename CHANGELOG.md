# Changelog

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
