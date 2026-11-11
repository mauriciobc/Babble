# Documentation policy

| | |
|---|---|
| **Title** | Babble documentation policy — what gets documented, who writes it, who approves it, and when it changes |
| **Identifier / version** | `BABBLE-POL-001` — v1.2 — in review |
| **Date of issue** | 2026-11-11 |
| **Author** | The Babble maintainers |
| **Approved by** | Pending: v1.2 is approved by the maintainer in the merge of the pull request that carries it, and that pull request is cited here at the merge. v1.1 was approved in the merge of [PR #108](https://github.com/mauriciobc/Babble/pull/108) (2026-11-11). The pull request is the record: it holds the diff, the review, the CI run and the merge. |
| **Scope** | Every document the project ships or keeps: the user manual, the in-app help pages, the reference and plan documents under `Docs/`, the working notes at the repository root, and the translation catalogues -- each in English and, where the set says so, in Brazilian Portuguese. It covers **how** those documents are planned, written, translated, reviewed, approved, versioned, distributed and retired. It does not cover the software itself, the release process, or what the documents say. |
| **Purpose** | To keep the documentation true of the software it describes, without paying for that with process the project cannot sustain. A policy that nobody follows is worse than none, because it is then cited as if it were. |
| **References** | ISO/IEC/IEEE 12207 (governance of the documentation process); ISO/IEC/IEEE 15289 (content and identification of information items); ISO/IEC 26514 (user documentation); ISO/IEC/IEEE 29148 (requirements). Project documents: [the user manual](manual/README.md), [Docs/README.md](README.md) (the index), [Docs/flathub.md](flathub.md) (a policy of the same kind, for a different subject). The Brazilian Portuguese edition of this policy is [Docs/pt_BR/documentation-policy.md](pt_BR/documentation-policy.md). |
| **Traceability** | Section 3 names the documentation set; section 12 maps the documents to the code they describe. |
| **Review** | At each release, and whenever the process itself changes. |

## 1. Why this document exists

The project writes a lot of documentation: a user manual, two languages of in-app help, twenty-eight files under `Docs/`, three reference documents at the repository root, and four translation catalogues. That volume has a failure mode, and it is not "too little documentation" — it is **documentation that has drifted from the software**. A feature ships, the help page does not mention it, a label changes and the manual keeps naming the old one, a plan document describes an architecture that was cut two releases ago. Nothing breaks; the documentation just stops being true.

This policy exists to make that failure visible and cheap to prevent. It follows ISO/IEC/IEEE 12207's six activities — plan, produce, review and approve, manage configuration, distribute, maintain — mapped onto a small project where one person maintains the code and AI assistants write much of the prose.

## 2. Proportionality

12207 asks for governance proportionate to the risk and size of the project. This project is small: one maintainer, one release train, no external documentation team. So:

- There is **no documentation board, no editorial board and no sign-off chain**. The roles in section 8 are roles one person can hold twice.
- There is **no separate review board for small changes**. A change that touches one help page is reviewed by the same person who wrote it, against the checklist in section 13.
- What is **not** scaled down is the mechanical part. Where a rule can be a test, it is a test, and it runs in CI. A rule that only exists in prose is the rule that gets broken.
- What is **not** scaled down is the record. An approval that cannot be pointed at later did not happen.

The policy says what is proportional; it does not say what is convenient.

## 3. The documentation set

What the project documents, who reads it, and when it is produced. "Standard" names the content norm each item is written against.

### 3.1 For users

| Item | Audience | Format | Standard | Produced when | Completeness criterion | Status |
|---|---|---|---|---|---|---|
| `README.md` | People deciding whether to install | Markdown | 26514 (orientation) | With every release | Says what the app does, what it needs, and how to run it | Current |
| `Docs/features.md`, `Docs/pt_BR/features.md` | Users and contributors | Markdown, English and pt-BR | 26514 | With every feature | Every feature listed, with the server version it needs | Current |
| `Docs/manual/`, `Docs/pt_BR/manual/` | Users | Markdown, English and pt-BR | 26514 + 15289 | With every user-visible change | Every procedure carries the full model; no broken cross-references; the label table is current; both editions carry the same sections | Current |
| `data/help/` | Users, inside the app | Mallard | GNOME help conventions | With every user-visible change | Every language set carries the same pages and ids; every cross-reference resolves | Current |
| `Docs/building.md`, `Docs/pt_BR/building.md` | Users building from source | Markdown, English and pt-BR | 26514 | When the build changes | Every dependency, the manifest, and the test suite | Current |
| `Docs/signing-in.md`, `Docs/pt_BR/signing-in.md` | Users | Markdown, English and pt-BR | 26514 | When the login flow changes | The scopes, both redirect paths, and what is stored | Current |
| `Docs/desktop-integration.md`, `Docs/pt_BR/desktop-integration.md` | Users and contributors | Markdown, English and pt-BR | 26514 | When an integration changes | Every GNOME feature, and what each needs | Current |
| `Docs/ROADMAP.MD` | Users and contributors | Markdown | — | With every release | What the project is betting on, and what it dropped | Current |

### 3.2 For contributors and maintainers

| Item | Audience | Format | Standard | Produced when | Completeness criterion | Status |
|---|---|---|---|---|---|---|
| `CLAUDE.md` | AI agents editing the code | Markdown | 15289 | With every change that breaks a convention | Architecture, commands, and the rules that are easy to break | Current |
| `AGENTS.md` | AI agents and contributors | Markdown | 15289 | With every change to the test harness | The harness, exactly as it is run | Current |
| `PERFORMANCE.md` | Contributors | Markdown | 15289 (report) | With every performance change | Baseline, current figure, and why each change was made | Current |
| `MEMORY-LEAKS.md` | Contributors | Markdown | 15289 (report) | With every leak found | The pattern, how to check it, and whether it is fixed | Current |
| `CATCH-UP.md` | Contributors | Markdown | 15289 (specification) | With every change to Catch Up | The feature's behaviour, specified | Current |
| `Docs/mastodon-compat.md` | Contributors | Markdown | 15289 | With every capability change | Every gate, and the server version behind it | Current |
| `Docs/localization.md` | Contributors and translators | Markdown | 26514 | With every change to the i18n setup | Languages, status, and how to add one | Current |
| `Docs/IMPROVEMENTS.md` | Contributors | Markdown | 15289 (record) | Continuously | Every finding, its decision, and what was deferred | Current |
| `Docs/glossary.md` | Contributors | Markdown | — | With every new concept the code is built around | Every domain term the code and the tests name, defined in plain words, agreeing with the manual's glossary where both name the same thing | Current |
| `Docs/*-plan.md`, `Docs/*-audit.md`, `Docs/*-study.md`, `Docs/*-review*.md` | Contributors | Markdown | 15289 (plan / report) | With the work they describe | The plan or the findings, with acceptance criteria | Some are historical; see section 11 |

### 3.3 For translators

| Item | Audience | Format | Standard | Produced when | Completeness criterion | Status |
|---|---|---|---|---|---|---|
| `po/README.md` | Translators | Markdown | 26514 | With a change to the translation workflow | How to translate, and what the tools are | Current |
| `po/*.po` | Translators | Gettext catalogue | — | With every string change | `msgfmt --check-format` clean; the `po check <lang>` test passes | Current |
| `data/help/<lang>/` | Translators | Mallard | GNOME help conventions | With every help page change | The page set matches the English one file for file | Current |

### 3.4 Formal items and working notes

Two kinds of document, with two different levels of rigour:

- A **formal item** is one that reaches a reader as a statement of what the software does. It carries the identification block of section 7, has an identifier and a version, and is approved under section 8. Today that is the user manual and this policy. Adding a third is a decision, not an accident.
- A **working note** is the project's process record: plans, audits, benchmarks, registers, the agent notes at the repository root. It does not need an identification block, because its audience is the people doing the work and its history is the repository's. Git is its history of record.

The line is not "important" versus "unimportant". It is "does this make a promise to a reader" versus "does this record what we are doing". A plan document that is read by nobody but its author is a working note even if it is long.

### 3.5 The two languages

The formal set is written twice: once in English, once in Brazilian
Portuguese. The rule is one sentence.

> **The English edition lives where the file already is. A Brazilian
> Portuguese edition lives beside it, under `Docs/pt_BR/`, mirroring the
> path exactly.**

So `Docs/features.md` has its translation at
`Docs/pt_BR/features.md`, and the manual's thirteen chapters are
`Docs/manual/NN-<english-slug>.md` and
`Docs/pt_BR/manual/NN-<portuguese-slug>.md`. The numbering prefix keeps
the two in the same order; the file names are translated, because a
reader opens them.

What follows from the rule:

- **The English tree is the source.** A change is made in English and
  translated, never the other way round. The Portuguese edition does not
  get ahead: when the two disagree, English is right and the translation
  is behind.
- **The two carry the same content.** The same sections, in the same
  order, saying the same things. A translation is a translation, not a
  rewrite: it does not add, drop or reorganise.
- **Anchors follow the language.** A link inside the English tree points
  inside the English tree, and one inside the Portuguese tree points
  inside the Portuguese tree. Cross-references are resolved when the
  translation is made, not left for a reader to discover.
- **The working notes stay in English.** Section 3.4 classifies them:
  they are the project's own record, read by the people doing the work.
  Translating twelve thousand lines of audit records and task lists would
  double the maintenance cost of every revision and reach nobody who
  does not already read the code.
- **Generated blocks stay generated.** The endpoint table in
  `mastodon-compat.md` is written by `src/test-api-registry.py` and is
  never edited by hand; in the Portuguese edition it is kept byte for
  byte, with the note saying so. The label table in chapter 10 maps
  English interface labels to their Portuguese ones, so it belongs to the
  Portuguese edition; the English edition says where the table is
  instead of repeating it in English.

## 4. Naming and versioning

### 4.1 Names

- Formal items: `BABBLE-<KIND>-<NNN>`, where kind is `MAN` (manual), `POL` (policy), `REF` (reference), `RPT` (report), `PLN` (plan).
- Files: lower case, hyphen separated (`mastodon-compat.md`, `documentation-policy.md`). The exception is the three reference documents at the repository root, whose names are established (`PERFORMANCE.md`, `MEMORY-LEAKS.md`, `CATCH-UP.md`) and are kept.
- The manual's chapters: `NN-<slug>.md`, numbered in reading order, with `README.md` as the front matter. The slug is translated with the chapter.
- A Brazilian Portuguese edition: `Docs/pt_BR/<path>`, mirroring the English path. The directory is named after the locale, as `data/help/<lang>/` already is.
- Never `final`, `final_v2`, `new`, or a date in place of a version. The history lives in the version and in git, not in the file name.

### 4.2 Versions

Every formal item carries a version of the form `vMAJOR.MINOR` and a status.

| Change | Bump | Example |
|---|---|---|
| The content is corrected, without changing what it says about the software | MINOR | A procedure gains a missing step, a typo in a label |
| What it says about the software changes | MAJOR | A new chapter, a preference removed, a shortcut changed |
| The item is withdrawn | Status becomes `obsolete` | — |

The version in the identification block states the item's version, not the version of the software it describes. The software version it describes is a separate field, and it is the one that has to be right.

### 4.3 Status

`draft` → `in review` → `approved` → `obsolete`.

Only `approved` items are cited as normative from another document. A draft may be cited as a draft, with its status named.

## 5. Producing a formal item

1. **Classify it.** User-facing (26514), a statement of what the system must do (29148), or an item of project information (15289). A formal item in this project is user-facing or normative; it is not a requirements specification, because the project does not write those.
2. **Gather the inputs before writing.** Audience, scope, environment, constraints, and the source of every claim about the software. A gap is marked `[A DEFINIR: …]` in the exact place it belongs — never guessed, and never left to be discovered by a reader.
3. **Follow the structure of the applicable standard.** For a manual, that is the structure of ISO/IEC 26514: identification, summary, introduction, concepts, procedures, reference, troubleshooting, glossary, index, support. For every procedure, the model: objective, pre-conditions, numbered steps, result, and a pointer to the troubleshooting chapter.
4. **Verify every claim about the software against the code.** A label, a shortcut, a limit and a message are all checkable, and all of them change. Section 12 says where each kind of claim is checked.
5. **Apply the identification block**, and the change history.

The last two are where most of the value is. A manual that describes the software from memory is the thing this policy exists to prevent.

## 6. The standards this project uses

| Norm | What it governs here | Where it is applied |
|---|---|---|
| ISO/IEC/IEEE 12207 | The documentation process | This document |
| ISO/IEC/IEEE 15289 | The content and identification of an information item | Every formal item |
| ISO/IEC 26514 | User documentation | The manual, the help pages, `README.md`, the user-facing documents under `Docs/` |
| ISO/IEC/IEEE 29148 | Requirements | Not used: the project does not write requirements specifications, and this policy does not create one |

Using a norm does not mean reproducing it. These are distillations of the intent and the structure of each standard, not replacements for the published text. Where the project holds a specific edition of a standard, the numbering is adapted to it and any point that deserves checking against the published text is flagged in the item.

## 7. The identification block

Every formal item opens with one. The template:

```
Title:            <name of the item>
Identifier:       <BABBLE-KIND-NNN> — v<major.minor> — <status>
Date of issue:    <YYYY-MM-DD>          Author(s): <…>
Approved by:      <…>                    References: <…>
Scope:            <what it covers and what it does not>
Purpose:          <why it exists and for whom>
```

The rules that make it useful:

- **The approver is named and dated.** "The maintainer" is a role, not a name. An approval that cannot be pointed at later did not happen.
- **The evidence is the pull request.** The approval act is the merge into `main`; the number and URL of the pull request are the record. This is what the `Approved by` field cites.
- **The scope states what the item does not cover.** A document without that boundary is read as covering everything it does not mention.
- **The software version the item describes is named separately.** The item's version and the software's version are different things, and only the second one tells a reader whether the item still applies to their build.

## 8. Review and approval

### 8.1 The roles

| Role | Who | What they do |
|---|---|---|
| **Author** | Whoever writes the item — a maintainer, a contributor, or an AI assistant under the maintainer's direction | Writes it, verifies the claims, and hands it over with the checklist ticked |
| **Technical reviewer** | The maintainer, or a second reader | Checks the technical accuracy against the code, and the checklist in section 13 |
| **Approver** | The maintainer | Authorises the baseline; the merge is the act |
| **Configuration manager** | The maintainer | Cuts baselines, keeps the history, and retires what is obsolete |

An AI assistant is never the reviewer or the approver of its own writing. Its output is reviewed by a person against the checklist, exactly as any other contributor's.

When the author is the approver — which is the normal case in a one-person project — the review is a separate pass over the finished item against the checklist, done after the writing, not during it. The proportional rule is that the review happens; who does it is what varies.

### 8.2 The flow

```
author writes ─► technical review ─► approver ─► merge to main ─► baseline
      │                │                  │
      │                └── checklist fails ─► back to the author
      └── checklist fails ─► back to the author
```

### 8.3 Entry criteria — before the review

- The identification block is complete, with a named approver.
- Every claim about the software has been checked against the code, and the checks are recorded in the pull request.
- The mechanical gates are green: the suite, the linter, and the documentation test of section 9.
- The cross-references resolve: every link, every anchor, every table.
- No `[A DEFINIR: …]` is left in a section the reviewer is being asked to approve. A marked gap is acceptable in a draft; it is not acceptable in an item being approved.
- The change history records what changed and why.

### 8.4 Exit criteria — after the merge

- The item is part of the baseline.
- The index that lists it is updated (`Docs/README.md`, or `README.md` for the top-level items).
- The traceability in section 12 still holds, and any module the change touched is named.
- The next release notes say what changed in the documentation, when there is something to say.

### 8.5 The record

The pull request is the record: it holds the diff, the review conversation, the CI run, and the merge. The identification block cites it. A project that wants an approval trail it can read in ten years gets it from the repository, not from a separate document that can drift from it.

## 9. What is checked mechanically

Rules that can be a test are a test, and the test runs in CI. This is the part of the policy that is not negotiable by convenience.

| Rule | Checked by |
|---|---|
| The two languages of the help pages carry the same files, the same page and section ids, and resolvable cross-references | `src/test-docs.py` |
| Every language directory under `data/help/` is registered in `help_languages`, and every registered language has a directory | `src/test-docs.py` |
| Every file the meson install list names exists, for every registered language | `src/test-docs.py` |
| The manual's label table is the one the translation catalogue produces | `src/test-docs.py`, with `--write-table` to regenerate |
| The two documentation trees are in step: every formal item in one has its counterpart in the other, and the chapters of the manual carry the same sections | `src/test-docs.py` |
| Every link and anchor in the manual resolves | `src/test-docs.py` |
| Every chapter of the manual carries an identification block | `src/test-docs.py` |
| The code still behaves as the suite says it does | the meson suite |

The test proves each rule can fail on a synthetic input, as the project's other rule-tests do. A check that cannot fail is not a check.

## 10. Configuration control

### 10.1 Baselines

A **baseline** is the set of documentation approved at a release. The release is the cut: the version in `meson.build` is the baseline identifier, and the approved items at that moment are its contents.

Between baselines, `main` is the working state. A document on `main` that has not been through review is a draft, whatever its status field says.

### 10.2 Change control

Every change to any document is a pull request against `main`, reviewed and merged under section 8. There is no other way in: not a direct push, and not an edit on the working tree that skips the review.

A change that touches the code and its documentation goes in **one** pull request. A change to a label that renames it in the code, in the help page, in the manual and in the label table is one change, not four — and if the four cannot go together, the code change waits.

### 10.3 History

- The **repository history** is the change history of record for every document in the repository.
- The **change history table** in an item is the reader's summary: what changed, when, and why. It is not a second log to maintain.
- The **commit message** says what changed and why, in the imperative, and names the reason when there is one that is not obvious from the diff.

## 11. Distribution

| Item | Lives in | Reached by | Who may change it |
|---|---|---|---|
| The manual | `Docs/manual/` (English), `Docs/pt_BR/manual/` (Portuguese) | The repository; `Docs/README.md` and `Docs/pt_BR/README.md` link them | Anyone, through a pull request |
| The help pages | `data/help/<lang>/` | Installed with the application, under `share/help/<lang>/`; opened with F1 | Anyone, through a pull request |
| The reference and plan documents | `Docs/` (English), `Docs/pt_BR/` (Portuguese, for the formal set) | The repository; the two indexes list them | Anyone, through a pull request |
| The working notes | Repository root | The repository | Anyone, through a pull request |
| The translation catalogues | `po/` | The repository; built into the application | Anyone, through a pull request |

**`Docs/README.md` is the index**, and an item that is not listed there is not distributed. The root `README.md` points at it. Two levels, both maintained: the root one for people deciding whether to install, the index for everyone else.

The help pages are the only documentation that reaches a user who never opens the repository, which is why they are installed rather than merely kept.

## 12. Traceability

Each formal item names the code it describes. The manual does this in its chapter 12; the table below is the project-level view.

| Kind of claim in a document | Checked against | By |
|---|---|---|
| A user-interface label | `src/`, and `po/*.po` for the translation | The documentation test, for the label table; the code, for the label itself |
| A keyboard shortcut | `src/main.py`, `src/shortcuts-dialog.ui` | Reading the code; the suite's lifecycle test |
| A menu item | `src/window.ui`, `src/main.py` | Reading the code |
| A preference and what it does | `src/preferences.py`, `data/io.github.mauriciobc.Babble.gschema.xml` | Reading the code |
| An error message or a toast | `src/`, by its translation string | Reading the code; `src/test-ui-strings.py` for the string rules |
| A limit | The server's declaration (`max_characters`, `max_media_attachments`, …) | The server; the manual says so rather than stating a number as universal |
| A server-version dependency | `src/capabilities.py`, `src/api/` | `src/test-api-registry.py`, and the generated table of `Docs/mastodon-compat.md` |
| A behaviour | The suite | The meson suite |

A document that makes a claim about the software and does not say where the claim came from has a claim that will rot.

## 13. The conformance checklist

The reviewer runs this against the item before approving it. It is short on purpose.

**Every item:**

- [ ] The identification block is complete: identifier, version, status, date, named approver, scope, purpose.
- [ ] The scope says what the item does not cover.
- [ ] The change history records the change and why.
- [ ] The index that lists the item lists the current version.

**A user-facing item:**

- [ ] Every claim about a label, a shortcut, a menu item, a preference, a message or a limit was checked against the code, and the checks are named in the pull request.
- [ ] Every procedure carries the whole model: objective, pre-conditions, numbered steps, result, and a pointer to troubleshooting.
- [ ] Every step is imperative, and each one has an observable result.
- [ ] No instruction depends on an image or on a colour.
- [ ] Every cross-reference resolves, and every term used is defined in the glossary.
- [ ] The same term means the same thing everywhere in the item.

**A translated item:**

- [ ] The translation set carries the same files and the same ids as the language it was made from.
- [ ] Every string that is a user-interface label is translated through the catalogue, not hand-edited into the document.

**A change to the software:**

- [ ] The documentation that describes it changed in the same pull request, or the pull request says why not.

## 14. Maintenance

### 14.1 Triggers

A document is updated when its trigger fires. Not "sometime": on the change that fires it.

| Trigger | What must change |
|---|---|
| A user-interface label changes | The manual, the help page, and the label table |
| A shortcut changes | The manual's reference chapter, and the help page's keyboard page |
| A menu item is added or removed | The manual, and the help page it appears in |
| A preference is added, removed or changes meaning | The manual's preferences chapter, and the help page's settings page |
| A message or toast changes | The manual's message table |
| A limit changes | The manual's limits table |
| A new feature ships | `Docs/features.md`, the manual, and the help pages |
| A server capability gate moves | `Docs/mastodon-compat.md`, and the manual's limits table |
| The build changes | `Docs/building.md`, the flatpak manifest |
| The login flow changes | `Docs/signing-in.md`, the manual's first chapter |
| A string changes | The catalogue, and the manual's label table |
| A formal item changes in English | Its Portuguese edition, in the same pull request |
| A release is cut | A new baseline; the manual's software version |

### 14.2 Periodic review

- **At each release**, the chapters of the manual and the help pages that the release touched are re-read against the built application, not against the diff.
- **Once a year**, or whenever the process itself changes, this policy is reviewed against what actually happened.
- The plan, audit and study documents under `Docs/` are **working notes**. They are not maintained after the work they describe is done; they are dated, and the ones that describe finished work are marked historical in section 3.2.

### 14.3 Obsolescence and retirement

An item that no longer describes anything is marked `obsolete` in its identification block, with a line saying what replaced it. It is not deleted: the history of the project's decisions is worth more than the tidiness of a directory. A document that has been retired stays readable.

## 15. Health indicators

Four measures, all of them countable without a tool:

| Indicator | How to count | Target |
|---|---|---|
| Formal items carrying a complete identification block | `src/test-docs.py` | 100 % |
| `[A DEFINIR: …]` markers left in approved items | A search over `Docs/manual/` | 0 |
| Documents in `Docs/` not listed in `Docs/README.md` | A search over the index | 0 |
| Formal items in one language with no counterpart in the other | `src/test-docs.py` | 0 |
| Released features absent from the manual and the help pages | Reading the release against the manual | 0 |

The first is enforced by the suite. The second and third are cheap searches; the fourth is the one that needs a person, which is why it is written down.

## 16. What this policy does not do

- It does not require a document for every change. Most changes need none.
- It does not require the formal items to be long. The manual is long because the software has a lot of surface; a small feature does not need a long manual.
- It does not create a requirements specification, a process model or a compliance record.
- It does not replace the suite. The suite is the gate; this policy says which documents have to agree with it.

## 17. Change history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.2 | 2026-11-11 | The Babble maintainers | The inventory (section 3.2) lists `Docs/glossary.md`, a working note for contributors (the domain terms the code is built around; it stays in English, as section 3.5 says of working notes), and section 1 counts twenty-eight files under `Docs/` instead of twenty-seven. What the policy says is unchanged. |
| 1.0 | 2026-11-11 | The Babble maintainers | First version. Written after the user manual and the Portuguese help pages, which showed what was missing: a written-down rito, a named approver, and a test that keeps the documents true of the code. Approved in the merge of PR #107. |
| 1.1 | 2026-11-11 | The Babble maintainers | The documentation is written twice: a new section on the two language trees, the naming rule for a translation, and the mechanical check that holds the two editions together. |. Approved in the merge of PR #108.
