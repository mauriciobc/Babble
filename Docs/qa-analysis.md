# Babble 0.2.0 — QA report

Tested 27 October 2026, build `87fbf41` (`main`, version shown in About as 0.2.0).

This is a test report: what was exercised, what happened, and what could not be signed off. It is not a source review.

## Result

**Conditional.** The automated suite is green, and the welcome screen accepts a real server name and refuses `http://` with a clear toast. What is still open is the help versus the shortcut list, a few English strings in Brazilian Portuguese, and everything that needs an account. A server name cannot contain a space, so the spinner on `not a server` is not a sign-in failure.

Signed-in use (timelines, posting, Catch Up, notifications, media, a second account) was **not** explored against a server. Those areas stay unaccepted, except where an automated test already covers them.

## Environment

| | |
|---|---|
| Build | Uninstalled `builddir`, version 0.2.0 |
| Display | Headless Mutter 51.1, one 1280×1024 monitor, Cairo renderer |
| Toolkit | GTK 4.24.1, libadwaita 1.10 |
| Settings | In-memory, empty. No saved account, no keyring login |
| Languages tried | English, then Brazilian Portuguese (`LANGUAGE=pt_BR`, catalogues from this build) |
| Account | None. Nothing was posted, followed, or deleted |

The machine’s real Babble settings and keyring were not used.

## What “done” meant

Accepted only if a person could see it:

- A fresh launch offers a way to name a server, and refuses a bad one in words.
- While signed out, posting, search, reload, and notifications cannot run.
- Preferences, keyboard shortcuts, About, and Help open from the welcome screen.
- The shortcut list matches the places the help describes.
- Brazilian Portuguese does not leave English in the welcome screen or in Preferences.
- The project’s own suite passes.

Reading, writing, Catch Up, and moderation need an account. They were not given one here.

## How it was run

1. **Automated suite.** `meson test -C builddir -j1` under the same headless display CI uses.
2. **Exploratory session.** The real welcome window, with an empty account list. Each case below is something that was done to that window and read back from it (labels, whether a button would press, toasts, dialogs).

## Automated suite

**65 passed, 0 failed, 1 skipped.**

The skip is `streaming helpers`: it stopped with “no usable secret service for the keyring round trip” (exit 77). Everything else in that test had already printed `ok`. Desktop file, metainfo, GSettings, and the four translation catalogues (`de`, `es`, `fr`, `pt_BR`) were checked and passed. `timeline render` and `thread profile render` talked to the public `mastodon.social` and passed.

That suite is the regression net for timelines, the composer, notifications, login plumbing, Catch Up ranking, and preferences keys. It does not walk the signed-in window as a person would.

## Exploratory session

### Passed

| Case | What was done | What happened |
|---|---|---|
| First screen | Launch with no account | Welcome. Title “Welcome to Babble”, subtitle “Log in with your Mastodon account”. Not a timeline. |
| Empty server | Look at Continue before typing | Continue is disabled. Label is “Continue”. Hint: “For example, mastodon.social”. |
| Named server | Type `mastodon.social` | Continue turns on and reads “Continue with mastodon.social”. Five other servers are offered: mstdn.social, infosec.exchange, mstdn.jp, hachyderm.io, piaille.fr. |
| Clear the field | Delete the server name | Back to a disabled “Continue”. The suggestions hide. |
| Plaintext server | Type `http://mastodon.social` or `ftp://…` and press Enter | Continue disables. A toast says Babble only connects over HTTPS, and to enter the name without a scheme or with `https://`. No spinner, no browser. |
| Unreachable server | Enter `localhost`, `server.invalid`, `a`, and `bad_host` | Each shows a toast naming the address and why it failed (“Connection refused” or “Name or service not known”). Continue is usable again afterwards. |
| Signed out | Try New Post, Notifications, Reload, Search | All four stay disabled. |
| Shortcuts | Open Keyboard Shortcuts | The dialog opens, grouped into General, Timelines, Catch Up, Web Column, and Composer. |
| Preferences | Open Preferences | Dialog opens on Appearance, Reading, Timelines, and Notifications. |
| About | Open About Babble | Name Babble, version 0.2.0, developer Mauricio Castro, website `https://github.com/mauriciobc/babble`, plus What’s New, Troubleshooting, Credits, and the GPL notice. |
| Larger text | Set text size to Large on the welcome screen | The example hint grew (14 px tall to 16 px). |
| Help | Press the Help action (F1) | The help viewer (Yelp) starts. |

### Failed

| ID | Case | Steps | Expected | Actual |
|---|---|---|---|---|
| QA-3 | Places the number keys do not open | Open Keyboard Shortcuts and read Ctrl+1 through Ctrl+9. Compare with the places named in Help → Reading (Home, Catch Up, Local, Federated, and the rest of the sidebar). | The number keys follow those places, or the help says which ones they skip. | Ctrl+1 Home, Ctrl+2 Notifications, Ctrl+3 Conversations, Ctrl+4 Search, Ctrl+5 Favorites, Ctrl+6 Bookmarks, Ctrl+7 Hashtags, Ctrl+8 Explore, Ctrl+9 Local. Nothing opens Catch Up, Federated, or Lists. Help → Keyboard says Ctrl+1–Ctrl+9 “jump between places” and, in the list of shortcuts that work anywhere, “Alt+Right forward”. In the dialog, Alt+Right is only Forward inside the web column. |
| QA-4 | Brazilian Portuguese leftovers | Launch with the interface language Brazilian Portuguese. Read the welcome screen and Preferences. | Those strings in Portuguese. | The button is “Continuar” and almost all of Preferences is translated. Still in English: the welcome hint “For example, mastodon.social”; the description under “Links da web ao lado da linha do tempo” (“Show other links in a column instead of the browser. Pages keep no cookies once Babble closes”); the description under “JavaScript em páginas da web” (“Many sites do not work without it”). |

### Noted, not failed

- **A space is not a server name.** `not a server` and `has space` are not hostnames. Typing one and pressing Enter does start the spinner, and the session prints `TypeError: constructor returned NULL`, until Cancel. That is invalid input, not a failed sign-in. `???` likewise is not a server; Continue simply stays off. Neither is filed.
- **Close Window** is listed under Keyboard Shortcuts as “No Shortcut”. Closing still belongs to the window manager. Not treated as a product failure.
- Yelp, while opening Help, printed `Unmatched block element: section`. The viewer did start. The pages were not read through in the viewer, so this is not filed as a broken manual.
- A 360 px-wide window was not actually achieved: after the window was already on screen it stayed 1280 px wide. Narrow layout was **not tested**.

## Defects

### QA-3 — Number keys and the help disagree about places

**Severity:** Minor for everyday pointing use. Higher if you only use the keyboard.
**Priority:** Medium.

**Steps.** On the welcome screen, open Keyboard Shortcuts. Read Help → Keyboard.

**Expected.** Ctrl+1–Ctrl+9 match the places Help talks about, and Alt+Right is described only where it works.

**Actual.** The dialog’s number keys stop at Local. Catch Up, Federated, and Lists are places in the manual and are not in that list. Catch Up’s own keys (J, K, H, L) are listed; they move inside Catch Up, they do not open it. Help says Alt+Right goes forward from anywhere. The dialog shows that key only as Forward for the web column. Go Back (Alt+Left) is the global one.

The signed-in sidebar was not on screen during this session, so the order of the rows was not checked against the keys. The comparison is the shortcut dialog against the help.

### QA-4 — Brazilian Portuguese still shows English on the welcome screen and in two settings

**Severity:** Minor.
**Priority:** Medium for a Portuguese reader. The rest of Preferences in this pass was translated.

**Steps.** Start Babble in Brazilian Portuguese. Look at the welcome hint. Open Preferences and read the link settings.

**Expected.** Portuguese.

**Actual.**

- Welcome hint: “For example, mastodon.social” next to a button that does say “Continuar”.
- Under the translated web-link setting: “Show other links in a column instead of the browser. Pages keep no cookies once Babble closes”.
- Under the translated JavaScript setting: “Many sites do not work without it”.

German, Spanish, and French were not brought up on screen. The catalogue check only proves those files compile.

## Not tested

No account was signed in. The following were **not** accepted or rejected:

| Area | Why it stopped |
|---|---|
| Home, Local, Federated, lists, hashtags, Explore | Needs a login |
| Catch Up, including a saved snapshot | Needs a login |
| Notifications, mentions, announcements | Needs a login |
| Composer: post, reply, media, poll, content warning, draft, schedule | New Post is disabled until there is an account. The automated `compose widgets` test passed; that is not this session |
| Profiles, threads, boosts, favourites, bookmarks, search results | Needs a login |
| Filters, mutes, blocks, reports, profile editing | Needs a login |
| A second account, switching, log out | Needs a login |
| Collections, admin dashboard | Needs a server new enough, and a login |
| Live updates and reconnect | `streaming helpers` skipped; no signed-in timeline was left open |
| Drag and drop, paste, screenshot in the composer | Composer never opened |
| Window at 360 px, high contrast, reduced motion, a screen reader | Not run. Larger text was only checked on the welcome hint |
| German, Spanish, French on screen | Only Brazilian Portuguese was looked at |
| Flatpak, Flathub | Not built this time. The app is still documented as not on Flathub |

A full sign-off needs one real account (and a second, on another server) and a pass through those rows.

## Recommendation

Do not treat 0.2.0 as signed off for readers until someone has used it with an account. The welcome screen is fine for any real server name.

QA-3 is a documentation and keyboard mismatch, worth doing with the shortcut dialog open next to the help page. QA-4 is a translation pass on the strings that are still English in Brazilian Portuguese. The spinner on a name with a space is moot: that text is not a server name.

After that, the open work is a signed-in pass: post, reply, schedule, Catch Up, notifications, account switch, and log out, on a disposable account. The automated suite can stay the regression check; it is already green apart from the keyring skip.
