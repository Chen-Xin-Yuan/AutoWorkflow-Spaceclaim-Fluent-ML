# Bilingual README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create switchable Chinese and English README entry points for `ansys-scdmdoc-mcp` while preserving the existing Chinese content as a rollback/reference copy.

**Architecture:** Keep `README.md` as the Chinese entry point and add `README.en.md` as the English entry point, with reciprocal language links at the top. Preserve code blocks, command names, evidence labels, and repository-relative links so both documents remain operationally accurate.

**Tech Stack:** Markdown, PowerShell command examples, Node.js/npm project metadata.

**Spec:** Existing `README.md`, `AGENTS.md`, and `package.json` in the project directory.

## Global Constraints

- Modify documentation only; do not change source code, tests, dependencies, or external GitHub state.
- Do not include machine-specific ANSYS installation paths or proprietary CHM contents.
- Preserve the original Chinese README and create a sibling bilingual copy.
- Keep `official-doc`, `inferred`, and `runtime-verified` evidence boundaries explicit.

---

### Task 1: Produce the bilingual README

**Files:**
- Create: `README.bilingual.md`
- Create: `docs/superpowers/plans/2026-09-14-bilingual-readme.md`

- [x] Translate every user-facing explanatory section into English immediately after the Chinese text.
- [x] Preserve all commands, tool names, error strings, links, and acceptance criteria.
- [x] Keep the document usable for both Chinese- and English-speaking contributors.

### Task 2: Verify documentation consistency

**Files:**
- Test: `README.bilingual.md` against `AGENTS.md` and `package.json`

- [x] Confirm all referenced local links resolve inside the repository.
- [x] Confirm documented npm scripts match `package.json`.
- [x] Confirm no proprietary CHM, extracted HTML, credentials, or machine-specific paths were added.
- [x] Confirm the Chinese body of `README.md` is preserved; only the language-selector link was added.
