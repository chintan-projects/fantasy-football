# Claudish to English dictionary

Load this for a thorough translation pass. Each entry is a phrase Claude reaches for by default, followed by what to write instead. When the plain version needs a fact you don't have (a number, a name, a duration), write `[number?]` or `[who?]` and ask. Never invent the fact to make the sentence sound concrete.

The lists drift. "Delve" peaked in 2024; "load-bearing" arrived in 2026. Recognize the family and you'll catch next year's word too.

## 1. Metaphor as precision

Claude uses a physical or engineering metaphor where a plain statement would do. The metaphor feels precise to the writer and vague to the reader.

| Claudish | Plain |
|---|---|
| load-bearing | important, required, "X depends on it", "if this breaks, Y stops working" |
| seam, well-defined seam | the place to make the change, the boundary between X and Y |
| hard gate, gate | required step, blocker, "must happen before X" |
| the trap, footgun, sharp edge | an easy mistake, "this will break X if you Y" |
| already sprung | already happened, already broken |
| unwired, not wired up | not connected, not used anywhere |
| lands, landed | ships, merges, is done |
| clears, cleared | is approved, passes |
| surface (verb) | show, display, report |
| surface area | the number of things exposed, the number of ways to call it |
| blast radius | what breaks if this fails |
| north star | the goal |
| lever, knob, dial | option, setting, "the thing we can change" |
| unlock, the unlock | allow, make possible, "what makes X possible" |
| guardrail | limit, check, rule |
| first-class | fully supported, built in |
| orthogonal | separate, unrelated, independent |
| non-trivial | hard, "about [N] days of work" |
| trivial | easy, quick, "about an hour" |
| rough edges, sharp edges | known issues, known problems |
| the deliberate gap, deliberately missing | not built yet, left out on purpose because X |
| belt and suspenders | two checks, redundant on purpose |
| smoking gun | the cause, proof |
| happy path | the normal case |
| drift, drifted | got out of sync, no longer matches |
| bit rot | stopped working over time |
| tight coupling, loose coupling | depends on, doesn't depend on |
| primitive | building block, basic operation |
| invariant | a rule that must always hold |
| idempotent | safe to run more than once |
| hydrate | load, fill in |
| the contract | what X promises, what X expects |
| bikeshed | argue about small things |
| yak shave | side task |
| the wrong abstraction | doesn't fit, the wrong shape for this |
| plumbing | the connecting code |
| escape hatch | a way to bypass X |
| foot in the door, beachhead | first customer, first use case |
| the long pole | the slowest part, the thing that sets the deadline |
| table stakes | required, expected, "customers assume this" |
| moat | advantage competitors can't copy |
| flywheel | "X makes Y grow, which makes X grow" (say the actual loop) |
| paint the picture | explain, describe |
| does the heavy lifting | handles most of X |
| carries the argument | is the main reason |

## 2. Inflation

Words that assert importance or quality without saying anything. Delete, or replace with the plain verb or the number.

| Claudish | Plain |
|---|---|
| crucial, critical, vital, essential, key, pivotal | delete, or say why it matters in one clause |
| robust | works under X, handles X |
| comprehensive | complete, covers X |
| nuanced | complicated, depends on X |
| fascinating, interesting | delete |
| powerful | say what it does |
| seamless, seamlessly | works without extra steps, no setup |
| elegant | simple |
| significant, significantly | say the number |
| substantial | say the number |
| meaningful | say the number or the effect |
| leverage | use |
| utilize | use |
| facilitate | help, let |
| ensure | make sure, check |
| delve, dive into | look at, read |
| navigate | handle, deal with |
| foster | build, grow |
| empower | let |
| streamline | simplify, shorten |
| optimize | speed up, cut the cost of |
| holistic | whole, all of X |
| multifaceted | has several parts |
| cutting-edge, state-of-the-art | new, best on [benchmark] |
| game-changing, transformative | say what changes |
| best-in-class, industry-leading | say the number and the comparison |
| innovative | say what is new |
| scalable | handles [N] users/requests |
| performant | fast, [N] ms |
| enterprise-grade | say the feature (SSO, audit logs, SLA) |
| intuitive | easy to use, no training needed |
| frictionless | no extra steps |
| thoughtful, deliberate | intentional, or delete |
| bespoke, tailored, curated | custom, chosen |
| landscape, ecosystem, space | delete or name the thing (market, tools, companies) |
| journey | process, steps, experience |
| paradigm | model, approach |
| synergy | works together |
| tapestry, mosaic, symphony | delete the sentence |
| testament to | shows |
| stands as, serves as | is |

## 3. Chatbot bookends

Assistant language that leaks into text meant for a reader. Delete all of it. Start with the answer. End with the last fact.

| Claudish | Plain |
|---|---|
| That's a great / fascinating / good question | delete |
| Certainly! Absolutely! Great! Sure! | delete |
| You're absolutely right | "Right." or "Yes." or skip to the fix |
| I should mention, I should note | delete, just say it |
| It's worth noting, worth mentioning, worth calling out | delete, just say it |
| Note that, Notably, Importantly, Crucially, Interestingly | delete |
| To be clear, To clarify, Just to clarify | delete |
| Here's the thing, Here's the short version, Here's what's happening | delete |
| Let me walk you through, Let me explain, Let me break this down | delete |
| Quick note, Quick take, Honest take, Honest answer | delete |
| In summary, In conclusion, Overall, All in all, To sum up | delete the paragraph |
| Hope this helps, I hope this helps | delete |
| Let me know if you'd like me to elaborate / have questions | delete |
| If you'd like, I can... / Happy to... / Would you like me to... | delete, or make one specific offer with a verb ("I can set this up Thursday.") |
| Does this make sense? | delete |
| Feel free to | delete |
| As you can see | delete |
| As mentioned, As noted above | delete |
| Great progress! Nice work! | delete |

## 4. Hedges and throat-clearing

Keep a hedge only when the uncertainty is real. Then name what you're uncertain about.

| Claudish | Plain |
|---|---|
| That said, | But |
| arguably, it could be argued | delete |
| in a sense, in some sense | delete |
| somewhat, fairly, quite, rather, relatively | delete |
| I think, I believe (in an answer) | delete, unless flagging real doubt, then say what you'd check |
| generally, typically, usually | keep only if the exception matters, then name the exception |
| it depends | say on what |
| may or may not | say which, or say what decides it |
| to some extent, to a degree | delete |
| it's possible that | delete or say the probability |
| in most cases | say the cases where it doesn't hold |
| it's important to remember | delete |

## 5. Rhetorical moves

The register itself: building to a turn of phrase, contrast for drama, fragments for punch. Don't build. State the claim.

| Claudish | Plain |
|---|---|
| not X but Y / isn't just X, it's Y / this isn't about X, it's about Y | state Y |
| The real question is X | ask X directly |
| Full stop. Period. | delete |
| X is a feature, not a bug | X is intentional because Y |
| Drama fragment: "Not a detail. A design decision." | "This was a design decision." |
| Worth stating plainly: X | X |
| Bears repeating | delete |
| Think of it as X | say what it is |
| In other words, Put differently, Said another way | pick one phrasing, delete the other |
| Which is to say | delete |
| At a high level, At its core, Under the hood, In practice, In effect, Effectively | delete |
| Fundamentally, Ultimately, At the end of the day | delete |
| The X of Y ("the Stripe of insurance") | describe what it does |
| Colon reveal: "One thing: X." / "The catch: X." | X |
| The short version: X | X |
| The bottom line: X | X |
| More than just a X | a X that also does Y |
| Rule of three by reflex ("fast, reliable, and scalable") | list what's true, in whatever count is true |
| Rhetorical question then answer | just the answer |
| Staged candor: "Honestly?" "Truthfully," "Frankly," | delete |
| This matters because | usually delete; state the consequence as its own sentence |
| It's tempting to think X, but Y | Y |
| Some would say X. They're wrong. | Y |

## 6. Headings

A heading is a label. Readers scan it to decide whether to read the section. A clever heading makes them read it to find out what it's about, which defeats the purpose.

| Claudish | Plain |
|---|---|
| Auth: the deliberate gap | Authentication |
| Rough edges worth knowing | Known issues |
| What this means for you | (fold into body, or) Impact |
| The bottom line | (delete heading) |
| Where things stand | Status |
| How we got here | Background |
| The path forward | Next steps |
| A note on X | X |
| Why this is hard | Constraints |
| The shape of the fix | Fix |
| Under the hood | How it works |
| Getting your hands dirty | Setup |

Sentence case, not Title Case. No punctuation inside a heading.

## 7. Insider compression

Not a word list. A test. Read each sentence and ask: does this only make sense if the reader already knows the codebase, the history, or the joke?

Claudish: *Two stray SQLite files exist, the exact DATABASE_URL trap CLAUDE.md warns about, already sprung.*

Plain: *There are two extra SQLite files that can cause database path errors.*

Rules:

- After any file path, internal name, or acronym, say what it means for the reader in the same sentence or the next.
- If the reader is a customer or an exec, drop the path and keep the consequence.
- If the reader is an engineer on the repo, keep the path and still say the consequence.
- Never reference a prior warning, doc, or conversation as a shortcut. Say the thing again, in one sentence.

## 8. Formatting

| Claudish | Plain |
|---|---|
| Em dash (—) or en dash (–) as a connector | comma, period, or parentheses |
| Bold on phrases inside sentences | bold only on labels at the start of a list item, if at all |
| Title Case Headings | Sentence case headings |
| Nested bullets | flat list, or prose |
| Bullets where each item is a full paragraph | prose |
| A two-column table with short cells | prose: "X is Y. Z is W." |
| Emoji | none |
| Horizontal rules between sections | none |
| Every sentence its own paragraph | paragraphs of 2 to 4 sentences |
