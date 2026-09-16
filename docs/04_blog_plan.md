# Step 6 — the writeup: plan

**Step 6 goal (AGENTS.md):** the blog for the complete solution, planned separately from steps 1–5.
The artifact it describes is the repository as frozen at **`69dfb38`** (tag `step5-complete`); the post
must cite that state, not an earlier one.

**2026-09-16 amendment:** a solution review (R0–R9) found defects in this frozen state and a fix pass
addressed them (`docs/05_review_fixes_plan.md`) — most importantly, AC6 is now `PASS` (not `PARTIAL`):
the recovered partition's classes do draw as the letters J and S, the published solution's single-star
probing method is disclosed rather than hidden, and net 806's "undriven" finding stands but the
"nondeterministic" framing is withdrawn (nearby existing signals reproduce the message byte-exactly).
Before drafting the post, re-point this plan at the fix pass's own freeze tag
(`review-fixes-complete`) and read the corrected rows below rather than the 2026-09-14 originals, which
are kept for the historical record, not as current instructions.

**This document is the plan, not the post.** No prose from the post appears here beyond the opening line
the user specified.

---

## 1. Locked decisions (user, 2026-09-14)

| # | Decision | Choice |
|---|---|---|
| B1 | Venue | `sraghup7/blogs` — a **public** GitHub Pages site for all the user's blog posts; this post at `https://sraghup7.github.io/blogs/janestreet-asic-puzzle/`. Jekyll + `minima`, permalink `/:title/`, so a post is one markdown file. |
| B2 | Reference post | The user's reference (`mummanajagadeesh.github.io/blogs/janestreet-asic-puzzle/`) **is the published solution** — the exact writeup `docs/02_known_solution.md` studies. Its **form** is the target (structure, voice, weight). Its **content is not reused**, and its two refuted claims are answered in a dedicated section (§7 below). |
| B3 | Honesty scope | The repo's negatives are published, in a "what I did not prove" section. **Corrected 2026-09-16 (see amendment above):** AC6 is `PASS` — the map does read "JS" in our recovery, and the method (single-star probing) is disclosed as the published solution's own; what remains unclaimed is that the chip's verdicts *confirm* this partition over every possible look-alike (the accepted input is unique, so they cannot distinguish it from one), and that one character of `TWO NOT TOUCH` follows the chip's undriven net 806 — genuinely undetermined by the layout, though nearby existing signals reproduce it. |
| B4 | AI disclosure | **The code was written by DeepSeek driving Hermes Agent**, stated in the post. **No claim of any kind about how the post itself was written** — a deliberate scoping decision by the user, recorded here so it is not "corrected" later. Jane Street's own request about AI (solvers were asked not to feed the puzzle to AI tools and not to use AI for the writeup, while AI for solving scripts was allowed) is recorded in `docs/01_problem.md` §2.1 with the user's non-binding decision; the post does not relitigate it. |
| B5 | Voice | First person, singular ("I"), throughout. Technical but plain: define each term at first use, no unexplained acronyms. |
| B6 | Length | 1,500–2,500 words, matching the reference's weight. |
| B7 | Figures | Generated from the repository's own artifacts by a committed script; plus the user's `warmup_gds_3d.mp4`. No figure may depict something we did not measure (§5). |

---

## 2. Structure

The user's flow, with the evidence each section is allowed to use. Section headings are indicative.

| § | Section | What it says | Evidence it may cite |
|---|---|---|---|
| 1 | Opening | The user's line: "I know I'm late to the party, but here is the methodology I used to tackle Jane Street's ASIC reverse-engineering problem." Then: what this post claims (a method, independently derived) and what it does not (not a competitive entry; submissions closed 2026-09-04). | `docs/01_problem.md` §2.1 |
| 2 | Jane Street and the puzzle | Short: who they are, what they shipped (a GDSII layout and nothing else), the "open ASIC reverse-engineering problem" framing, and the second competition (design your own chip) that follows. | `docs/01_problem.md` §1–2 |
| 3 | The problem in my own words | The chip reads a 121-bit bitmap serially and answers with a message; `success` is the verdict; a 27-cell warm-up design with its correct netlist and a reference VCD are provided. Frame the real task: **there is no netlist — only polygons**, so the job is to recover one and then find the input the chip accepts. | `docs/01_problem.md` §3; `tools/target.py` (`FEED_ORDER`, `SUCCESS_CYCLE = 126`) |
| 4 | The approach | The four moves that make it *our* route rather than the published one: (a) the chip describes itself — layer roles and pin geometry from the GDS's own standard-cell masters, so there is no technology file to get wrong; (b) connectivity by KLayout's own engine, driven by that derived stack; (c) the hidden rule is **measured**, not assumed; (d) the answer is **derived** by our own search, with two independently written enumerators. Plain-language Δ1–Δ6 from the plan. | `docs/03_our_plan.md` §3 (Δ1–Δ6), §6 |
| 5 | Tech stack | Python 3.11, `gdstk`, `klayout`, `matplotlib`, `iverilog`/`vvp`; and what is deliberately absent — no SkyWater PDK, no LEF, no `shapely`, no SymbiYosys/bitwuzla. Give the reason (reduced dependency surface; fewer ways to be wrong), and state the AI tool used (B4). | `requirements.txt`, `docs/deps.md`, `tools/checks/check_hygiene.py` |
| 6 | The solving workflow | Five steps, each with the number that proves it: (i) 69 masters → pin names and pin geometry, calibrated against the warm-up's known netlist; (ii) 1618 placements, all on the site grid, only 0°/180°, transform convention fixed empirically by `transform(bbox) == global bbox` for all 1618; (iii) connectivity — 40 360 conductor shapes, each in exactly one net, 2 626 nets, supply identified **by pin name**; (iv) 2 777 of 2 777 functional pins mapped to exactly one net; (v) behavioural models validated twice — the warm-up's real netlist *and* byte-exact replay of the provided VCD (2 808 bits, 0 mismatches); then the solve. | `docs/03_our_plan.md` §7 outcome blocks (A1–D), `recon/derived/*.json` |
| 7 | Where the chip's rule came from, and the answer | The hidden constraint story: eleven counters that turn out to be the eleven **column** counters, no per-region decode anywhere in the netlist, so the partition is recovered by measurement (single-star sweeps → trigger sets → exact cover → capacity-2 classes) and then corroborated through the chip's *message* (23 boards spell `TWO NOT TOUCH`, 156 controls that also break a class cap do not — all 189 boards of the swap family are fed, not a sample). The recovered classes do draw as the letters **J** and **S**. Then the answer: the 121-bit vector, `success` at cycle 126, `(* TWO STARS *)`. | `docs/steps/C4.md`, `recon/derived/c4_partition.json`, `e2_messages.json`, `solutions.json`, `acceptance.json` |
| 8 | Verification | How the claims are held: 33 gates, each re-deriving what it asserts; fault injection as a meta-gate (mutate an artifact → require at least one gate to fail, and record which); byte-reproducibility (`reproduce --cold`: delete every tracked derived artifact, rebuild from the upstream layout, get the same bytes — 32 of 32, 30 stages, ~7 minutes). Include the defects the process caught in its own evidence, including two found by the post-freeze solution review's fix pass. | `tools/checks/run_all.py`, `fault_inject.py`, `repro.py`, `docs/verification.md` |
| 9 | Where my measurements disagree with the published writeup | Two claims, stated as measurements, not opinions: (a) the map — the published method (probe one cell at a time and watch a per-region counter) targets per-region counters that this netlist does not contain; its eleven counters are the eleven **column** counters; the fix pass disclosed that our own recovery in fact uses that same single-star probing method (prohibited method 5), stated plainly rather than presented as a symbolic decode; (b) the published hindsight lesson — "whatever broke on the 738-cell netlist also broke on the 27-cell warmup" — is a useful heuristic but not a rule: the two defects that cost this project the most were *invisible* on the warm-up (a per-master pin assignment the warm-up cannot check, and a net key that was unique only within one circuit). | `docs/02_known_solution.md` (D-series), `docs/verification.md`, `docs/net806.md`, `docs/steps/C4.md` (R18), `docs/03_our_plan.md` §2 (P5 disclosure) |
| 10 | What I did not prove | **Corrected 2026-09-16:** AC6 is `PASS`, not `PARTIAL` — the recovered map's classes do read "JS", the chip corroborates it on all 189 boards of the swap family, and none of 2000 look-alike partitions reproduces that agreement. What remains unproven: the accepted input is unique, so the chip's verdicts alone cannot *confirm* this partition over an unseen look-alike (188 of 200 look-alike partitions were indistinguishable under simple rejection); and one character of `TWO NOT TOUCH` follows the chip's one genuinely undriven net (806) — the layout does not determine what it carried, though nearby existing signals (5 of 24 candidates within 15 µm) reproduce the message byte-exactly. Not "nondeterministic" — undetermined by the layout, which is a narrower and more defensible claim. | `recon/derived/acceptance.json`, `net806.json`, `docs/net806.md`, `docs/verification.md` §16, §25 |
| 11 | Closing | What the exercise was worth: a netlist recovered from polygons alone, an answer derived rather than transcribed, and a verification story where every number is machine-checked. One line on the five things the repo refuses to claim. | `docs/03_our_plan.md` §11, `README.md` |

**Flow check against the user's request:** Jane Street and the problem → statement in our own words →
approach → tech stack → solving workflow → verification → conclusion, with the disagreements and the
negatives inserted before the conclusion because B2/B3 require them.

---

## 3. What the post must not do

1. **No invented numbers.** Every figure in the post is copied from a committed artifact or a gate line.
2. **No claim that the region map is *confirmed*.** We recovered a partition and the chip's message
   corroborates it on all 189 boards of the swap family, with none of 2000 look-alikes reproducing that
   agreement — but the accepted input is unique, so the chip's verdicts alone cannot rule out an unseen
   look-alike. **Corrected 2026-09-16:** the spelling *is* reproduced — the recovered classes do draw
   as "JS" — so a figure of the map may show that, captioned as measured and corroborated, not proven.
3. **No reuse of the published writeup's text, code or narrative**, and no framing of its approach as
   ours. Where it is mentioned, it is named as the published solution and answered with a measurement.
4. **No claim about how the post was written** (B4).
5. **No claim that this was a competition entry** — submissions closed 2026-09-04.
6. No promise of code the reader cannot get: the upstream puzzle files are not redistributed in the repo
   (gitignored, re-clonable from Jane Street), so a "reproduce this" pointer names the repo layout
   rather than pretending to be a self-contained download.

---

## 4. Figures

Generated by `scripts/make_figures.py` **in the blog repository** (not in the frozen project repo),
reading the project's committed artifacts by path and recording the cited commit id in a header row of
the metadata file it writes. Every figure is therefore sourced: artifact + script + commit.

| # | Figure | Source | Shows |
|---|---|---|---|
| F1 | Pipeline flow | `tools/puzzle/cli.py::STAGES` (30 stages) | the four phases as branches, and where the two independent oracles (warm-up netlist, reference VCD) bite — not a decorative flowchart |
| F2 | The answer | `recon/derived/solutions.json`, `e1_messages.json` | the 121-bit board as an 11×11 grid, the message `(* TWO STARS *)`, `success` at cycle 126 |
| F3 | The message table | `e1_messages.json`, `e2_messages.json` | the five messages and what triggers each, including `TWO NOT TOUCH` on 23 constructed boards vs 156 controls (all 189 of the swap family tested, not a sample) |
| F4 | The recovered partition | `recon/derived/c4_partition.json` | the 11 classes over the 11×11 grid, two stars of capacity each — **as measured**, corroborated by the chip, captioned as recovered rather than confirmed (B3 guard); may show the "JS" reading since it is now reproduced |
| F5 | Net 806 | `recon/derived/net806.json` (+ a geometry render from `puzzle.gds`) | the wire (17 shapes, met1–met3), its four vias, the one foreign cut that cannot merge, and the two message characters it decides |
| F6 | Warm-up render | the user's `warmup_gds_3d.mp4` (4.7 MB, `assets/video/`) | video, embedded with `<video controls>`; the mp4 is the user's own render, credited as such |

Format: PNG at 2× for retina (`matplotlib` `dpi=200`), `assets/img/`. Filenames carry the figure number
(`f1_pipeline.png`) so the post's references are stable.

---

## 5. Verification of this step

The step is done when all of the following are true and shown:

1. `https://sraghup7.github.io/blogs/janestreet-asic-puzzle/` returns the post, and the home page lists it;
2. every number in the post matches a committed artifact or gate line — checked by a script that greps
   the post for the figures it cites and compares each against the artifact it names
   (`scripts/check_post_numbers.py` in the blog repo), so the post cannot drift from the repo;
3. the post contains no *confirmation* claim for the map — that it is proven the unique rule, or that
   the chip's verdicts alone rule out every look-alike (a plain grep, in the same script, for the
   sentence patterns that would break B3); a "JS" claim is permitted, since it is now reproduced;
4. the video and every figure load from the published URL (not just locally);
5. the project repo is unchanged by this step except for this plan document and its links.

---

## 6. Delivered so far

* `sraghup7/blogs` created, public, Pages enabled and **live** (`built / no error`, HTTP 200,
  Jekyll 3.10.0, `feed.xml` 200) with `_config.yml`, `index.md`, `README.md`, `.gitignore`.
* `assets/video/warmup_gds_3d.mp4` committed (the user's render).
* Note for the draft: the README quotes one measured run (420.7 s); the user's own re-verification of
  the same command measured 434.5 s. The post says "about seven minutes" and cites the README's table
  rather than asserting a single exact figure.
