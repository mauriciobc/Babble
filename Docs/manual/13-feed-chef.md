# 13 — Feed Chef: recipe timelines

> **Document:** `BABBLE-MAN-001` — chapter 13 — v1.1 — approved
> **Software described:** Babble 0.4.0 · **Date:** 2026-11-24 · **Parent:** [The Babble User Manual](README.md)

`Feed Chef` is a timeline you design yourself. Where `Catch Up` looks back over what you already follow, Feed Chef keeps cooking a live feed from a *recipe* you control.

## 13.1 What it is

A **recipe** has three parts:

1. **Pantry** — where posts come from: `Home`, `Local`, `Federated`, or one of your lists, with hashtags to include or exclude.
2. **Diet** — what a post must pass: blocked words, media rules, alt text, sensitivity, tone, a minimum length, or only people you follow.
3. **Kitchen** — how matching posts are ordered and balanced: engagement, freshness, trending topics, a limit per person or server, and grouping of similar posts.

The result is a continuous timeline, not a frozen snapshot. You can pin several recipes to the sidebar, edit them, preview them, and share them.

Feed Chef is **optional**. Preferences → Timelines → Sidebar → `Feed Chef` turns it on or off. When it is off, Babble does not load it at all: no sidebar entries, no background work and no network requests for it. Turning it off closes its open pages at once; the memory of its code is freed after a restart.

## 13.2 Creating a recipe

**Objective:** build a Feed Chef timeline of your own.

**Pre-conditions:** you are signed in, and `Feed Chef` is enabled in Preferences.

1. Open `Feed Chef` in the sidebar (or the list of recipes it shows when you have none yet).
2. Choose `New Recipe`, then a template or `Blank Recipe`.
3. In the recipe builder, set the pantry, diet and kitchen controls. The builder counts matching posts as you change them.
4. Optionally click `_Preview` / `_Preview Recipe` to see a sample of the result without saving.
5. Save the recipe.

**Result:** the recipe appears in your Feed Chef list and can be opened as a timeline.

**If it goes wrong:** a recipe that cannot be saved shows `Could not save the recipe`. Preview never writes the recipe; closing the builder without saving discards the changes (`Your changes to this recipe will be lost.`).

## 13.3 Reading a recipe timeline

Open a recipe from the Feed Chef list, or from its pinned place in the sidebar.

- Posts among the most engaging can show a `Popular` badge.
- A **heat level** (`Heat Level`: `Cool`, `Warm`, `Hot`, `Fire`) hides a share of the quieter posts so the feed stays sharper.
- New posts that score well can wait behind a pill instead of jumping the page.
- `_Why This Post?` on a post opens `Why This Post?` and explains why it was kept or removed for this cook.
- `Kitchen Report` summarizes recent cooks for that recipe.
- `More _Like This` on a post focuses the timeline on its hashtags and words (`Showing more like %s`).

Everything Feed Chef records for those explanations stays **on your computer**. You can clear that audit history from Feed Chef without deleting the recipes themselves.

## 13.4 Pinning, sharing and importing

**Pin.** On a recipe timeline, use `Pin to Sidebar` / `Unpin from Sidebar` like any other place.

**Share.** `Share Recipe` (or `S_hare Recipe`) opens a dialog with the share link and, when the recipe is small enough, a QR code. In that dialog, `_Copy Link` copies the link; a recipe that is too large for a QR code says so and still lets you copy the link. From the recipe list menu, `_Copy Share Link` copies the same link without opening the dialog.

**Import.** Opening a Feed Chef link asks before anything is written: `Import Recipe?` / `Import “%s” into this account?`, with `_Import`. If a recipe of the same identity already exists, Babble asks `Replace Recipe?` first. Nothing is imported until you confirm.

## 13.5 Turning Feed Chef off

**Objective:** stop Feed Chef from using memory or the network.

1. Open Preferences → Timelines → Sidebar.
2. Turn off `Feed Chef` (subtitle: *"Recipe timelines; uses more memory and network. Turning it off closes its pages now and frees the rest after a restart"*).

**Result:** Feed Chef leaves the sidebar, its open pages close, and it makes no further requests. After you restart Babble, its code is no longer kept in memory either.

## 13.6 Summary

| Question | Answer |
|---|---|
| How is this different from Catch Up? | Catch Up is a ranked look-back over Home, frozen as a snapshot. Feed Chef is a live recipe you design. |
| Where do I turn it off? | Preferences → Timelines → Sidebar → `Feed Chef`. |
| Does sharing upload my recipe to Babble’s servers? | No. The share link encodes the recipe; import asks before writing it into an account. |
| Where are the “why this post” logs kept? | On your computer only. |
