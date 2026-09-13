#!/usr/bin/env python3
"""Flag Claudish in a draft: metaphor-as-precision, inflation, chatbot bookends,
rhetorical moves, em dashes, long sentences, and clever headings.

Usage:
    python3 claudish_lint.py draft.md [more.md ...]
    cat draft.md | python3 claudish_lint.py -
    python3 claudish_lint.py --strict draft.md   # warnings count as errors

Exit code 1 when any error-level finding exists (or any finding with --strict).
Fenced code blocks and inline code are skipped. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

Severity = Literal["error", "warn"]

MAX_SENTENCE_WORDS_WARN = 25
MAX_SENTENCE_WORDS_ERROR = 35
MAX_HEADING_WORDS = 6
BOLD_PER_100_WORDS_WARN = 3.0


@dataclass(frozen=True)
class Finding:
    """One lint result."""

    line: int
    severity: Severity
    message: str


# (pattern, replacement hint, severity). Case-insensitive, word-bounded.
PHRASES: list[tuple[str, str, Severity]] = [
    # 1. Metaphor as precision
    (r"load[- ]bearing", "say what depends on it", "error"),
    (r"\bseam\b", "the place to change / the boundary", "error"),
    (r"\bhard gate\b", "required step / blocker", "error"),
    (r"\bthe trap\b", "the easy mistake, and what it breaks", "error"),
    (r"\bfootgun\b", "an easy mistake", "error"),
    (r"\bsprung\b", "already happened", "warn"),
    (r"\bunwired\b", "not used anywhere", "error"),
    (r"\bblast radius\b", "what breaks if this fails", "error"),
    (r"\bnorth star\b", "the goal", "error"),
    (r"\bthe unlock\b", "what makes X possible", "error"),
    (r"\bnon-?trivial\b", "hard / about N days", "error"),
    (r"\brough edges\b", "known issues", "error"),
    (r"\bsharp edges?\b", "known problems", "warn"),
    (r"\bdeliberate gap\b", "not built yet, on purpose because X", "error"),
    (r"\bbelt and suspenders\b", "two checks", "error"),
    (r"\bsmoking gun\b", "the cause / proof", "error"),
    (r"\bsurface area\b", "the number of things exposed", "warn"),
    (r"\bfirst-class\b", "fully supported / built in", "warn"),
    (r"\borthogonal\b", "separate / unrelated", "warn"),
    (r"\bguardrails?\b", "limit / check", "warn"),
    (r"\blevers?\b", "option / setting", "warn"),
    (r"\bthe long pole\b", "the slowest part", "warn"),
    (r"\btable stakes\b", "required / expected", "warn"),
    (r"\bheavy lifting\b", "handles most of X", "warn"),
    (r"\bcarr(?:y|ies) the argument\b", "is the main reason", "error"),
    (r"\bhappy path\b", "the normal case", "warn"),
    # 2. Inflation
    (r"\bcrucial(?:ly)?\b", "delete, or say why", "error"),
    (r"\bpivotal\b", "delete", "error"),
    (r"\bvital\b", "delete", "warn"),
    (r"\brobust\b", "works under X", "error"),
    (r"\bnuanced\b", "complicated / depends on X", "error"),
    (r"\bfascinating\b", "delete", "error"),
    (r"\bseamless(?:ly)?\b", "no extra steps", "error"),
    (r"\bleverag(?:e|es|ing)\b", "use", "error"),
    (r"\butiliz(?:e|es|ing)\b", "use", "error"),
    (r"\bfacilitat(?:e|es|ing)\b", "help / let", "error"),
    (r"\bdelv(?:e|es|ing)\b", "look at", "error"),
    (r"\bempower(?:s|ing)?\b", "let", "error"),
    (r"\bstreamlin(?:e|es|ing)\b", "simplify / shorten", "error"),
    (r"\bholistic\b", "whole", "error"),
    (r"\bmultifaceted\b", "has several parts", "error"),
    (r"\bcutting-edge\b", "new / best on [benchmark]", "error"),
    (r"\bgame-chang(?:er|ing)\b", "say what changes", "error"),
    (r"\btransformative\b", "say what changes", "error"),
    (r"\bbest-in-class\b", "say the number", "error"),
    (r"\bindustry-leading\b", "say the number", "error"),
    (r"\benterprise-grade\b", "name the feature", "error"),
    (r"\bfrictionless\b", "no extra steps", "error"),
    (r"\bsynerg(?:y|ies|istic)\b", "works together", "error"),
    (r"\btapestry\b", "delete the sentence", "error"),
    (r"\btestament to\b", "shows", "error"),
    (r"\b(?:ever-)?evolving landscape\b", "delete", "error"),
    (r"\blandscape\b", "delete or name the thing", "warn"),
    (r"\bjourney\b", "process / steps", "warn"),
    (r"\bparadigm\b", "model / approach", "warn"),
    (r"\bsignificant(?:ly)?\b", "say the number", "warn"),
    (r"\bcomprehensive\b", "complete / covers X", "warn"),
    (r"\bpowerful\b", "say what it does", "warn"),
    (r"\bscalable\b", "handles N users", "warn"),
    (r"\bperformant\b", "fast / N ms", "warn"),
    (r"\bintuitive\b", "easy to use", "warn"),
    (r"\bthrilled\b", "delete", "error"),
    (r"\bunveil(?:s|ing)?\b", "launch / release", "error"),
    (r"\breimagin(?:e|es|ing)\b", "say what is different", "error"),
    # 3. Chatbot bookends
    (r"\b(?:great|good|fascinating|excellent) question\b", "delete", "error"),
    (r"\bi should (?:mention|note)\b", "delete, just say it", "error"),
    (r"\b(?:it'?s )?worth (?:noting|mentioning|calling out|stating)\b", "delete", "error"),
    (r"\bit'?s important to (?:note|remember)\b", "delete", "error"),
    (r"\bnote that\b", "delete", "warn"),
    (r"\bto be clear\b", "delete", "error"),
    (r"\bhere'?s the thing\b", "delete", "error"),
    (r"\bthe short version\b", "delete", "error"),
    (r"\bhonest (?:take|answer)\b", "delete", "error"),
    (r"\bquick (?:note|take)\b", "delete", "warn"),
    (r"\blet me (?:walk you through|explain|break this down)\b", "delete", "error"),
    (r"\bin (?:summary|conclusion)\b", "delete the paragraph", "error"),
    (r"\bhope this helps\b", "delete", "error"),
    (r"\blet me know if\b", "delete", "error"),
    (r"\bhappy to\b", "delete, or one concrete offer", "warn"),
    (r"\bfeel free to\b", "delete", "warn"),
    (r"\byou'?re absolutely right\b", "Right. / Yes.", "error"),
    (r"^\s*(?:certainly|absolutely|great|sure)[!.,]", "delete", "error"),
    # 4. Hedges
    (r"\bthat said\b", "But", "warn"),
    (r"\barguably\b", "delete", "warn"),
    (r"\bit could be argued\b", "delete", "error"),
    (r"\bin (?:a|some) sense\b", "delete", "warn"),
    (r"\bto some extent\b", "delete", "warn"),
    # 5. Rhetorical moves
    (r"\bisn'?t just\b", "state Y", "error"),
    (r"\bnot (?:just|only|merely) (?:a |an |about )?\w+[,;]? (?:but|it'?s)\b", "state Y", "error"),
    (r"\bthe real question\b", "ask it", "error"),
    (r"\bfull stop\b", "delete", "error"),
    (r"\ba feature,? not a bug\b", "X is intentional because Y", "error"),
    (r"\bthink of it as\b", "say what it is", "error"),
    (r"\bput differently\b", "pick one phrasing", "error"),
    (r"\bin other words\b", "pick one phrasing", "warn"),
    (r"\bat a high level\b", "delete", "error"),
    (r"\bat its core\b", "delete", "error"),
    (r"\bunder the hood\b", "delete / how it works", "warn"),
    (r"\bin practice\b", "delete", "warn"),
    (r"\bfundamentally\b", "delete", "warn"),
    (r"\bat the end of the day\b", "delete", "error"),
    (r"\bthe bottom line\b", "delete", "error"),
    (r"\bmore than just\b", "say what it also does", "error"),
    (r"\bbears repeating\b", "delete", "error"),
    (r"^\s*honestly[,?]", "delete", "error"),
    (r"\bit'?s tempting to\b", "state the claim", "warn"),
]

COMPILED: list[tuple[re.Pattern[str], str, Severity]] = [
    (re.compile(p, re.IGNORECASE), hint, sev) for p, hint, sev in PHRASES
]

DASH_RE = re.compile("[—–]|\\s--\\s")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*)$")
BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
WORD_RE = re.compile(r"[A-Za-z0-9'’\-]+")


def prose_lines(text: str) -> Iterator[tuple[int, str, bool]]:
    """Yield (line_number, line_without_code, is_heading), skipping fenced code."""
    in_fence = False
    for number, raw in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = INLINE_CODE_RE.sub("", raw)
        yield number, stripped, bool(HEADING_RE.match(raw))


def check_phrases(number: int, line: str) -> Iterator[Finding]:
    """Flag every Claudish phrase on a line."""
    for pattern, hint, severity in COMPILED:
        match = pattern.search(line)
        if match:
            yield Finding(number, severity, f'"{match.group(0)}" -> {hint}')


def check_dashes(number: int, line: str) -> Iterator[Finding]:
    """Flag em dashes, en dashes, and spaced double hyphens."""
    if DASH_RE.search(line):
        yield Finding(number, "error", "dash used as connector -> comma, period, or parentheses")


def check_heading(number: int, line: str) -> Iterator[Finding]:
    """Flag clever or long headings."""
    match = HEADING_RE.match(line)
    if not match:
        return
    title = match.group(1).strip()
    words = WORD_RE.findall(title)
    if len(words) > MAX_HEADING_WORDS:
        yield Finding(number, "warn", f"heading has {len(words)} words -> use a plain noun")
    if re.search(r"[:—–?!]", title):
        yield Finding(number, "warn", "heading has punctuation -> use a plain noun")
    if re.search(r"\b(?:worth|deliberate|the thing|what this means|path forward)\b", title, re.I):
        yield Finding(number, "error", "clever heading -> use a plain noun")
    if title.istitle() and len(words) >= 3:
        yield Finding(number, "warn", "Title Case heading -> sentence case")


def check_sentences(number: int, line: str, is_heading: bool) -> Iterator[Finding]:
    """Flag long sentences in prose lines."""
    if is_heading or not line.strip():
        return
    for sentence in SENTENCE_SPLIT_RE.split(line.strip()):
        count = len(WORD_RE.findall(sentence))
        if count > MAX_SENTENCE_WORDS_ERROR:
            yield Finding(number, "error", f"sentence has {count} words -> split it")
        elif count > MAX_SENTENCE_WORDS_WARN:
            yield Finding(number, "warn", f"sentence has {count} words -> consider splitting")


def check_bold_density(text: str) -> Iterator[Finding]:
    """Flag heavy bold use across the whole document."""
    words = len(WORD_RE.findall(text))
    bolds = len(BOLD_RE.findall(text))
    if words >= 100 and (bolds / words) * 100 > BOLD_PER_100_WORDS_WARN:
        yield Finding(0, "warn", f"{bolds} bold spans in {words} words -> bold only labels")


def lint(text: str) -> list[Finding]:
    """Run every check and return findings sorted by line."""
    findings: list[Finding] = []
    for number, line, is_heading in prose_lines(text):
        findings.extend(check_phrases(number, line))
        findings.extend(check_dashes(number, line))
        findings.extend(check_heading(number, line))
        findings.extend(check_sentences(number, line, is_heading))
    findings.extend(check_bold_density(text))
    return sorted(findings, key=lambda f: (f.line, f.severity))


def read_source(name: str) -> str:
    """Read a file path, or stdin when name is '-'."""
    if name == "-":
        return sys.stdin.read()
    return Path(name).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Flag Claudish in a draft.")
    parser.add_argument("paths", nargs="+", help="files to check, or - for stdin")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)

    failed = False
    for name in args.paths:
        findings = lint(read_source(name))
        label = "stdin" if name == "-" else name
        for finding in findings:
            print(f"{label}:{finding.line}: {finding.severity}: {finding.message}")
            if finding.severity == "error" or args.strict:
                failed = True
        errors = sum(1 for f in findings if f.severity == "error")
        warns = len(findings) - errors
        print(f"{label}: {errors} errors, {warns} warnings")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
