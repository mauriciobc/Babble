# 7 — Desktop integration

> **Document:** `BABBLE-MAN-001` — chapter 7 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

Babble was made for GNOME, and uses what GNOME offers instead of recreating it. This chapter lists every integration, what it does, and what it needs.

## 7.1 The web column beside the timeline

**Objective:** read a web page without leaving Babble.

**Pre-conditions:** WebKitGTK is installed, and the `Web Links Beside the Timeline` preference is on (Preferences → Reading → `Links` group).

1. Click a link in a post that is neither a post link nor a profile link.
2. The page opens in a column beside the timeline — or over it, if the window is too narrow.

**Result:** the column has a header with `Hide Web Column`, `Back`, `Forward`, the page title, and a `Page Actions` menu (`Reload`, `Open in Browser`, `Copy Link`).

**What the column does not do:**

**If it goes wrong:** if the link opens in the browser even with the preference on, WebKitGTK is not installed — in that case the option does not even appear in Preferences. If the page opens blank, turn `JavaScript on Web Pages` off and reload; many sites depend on it. See [11.12](11-troubleshooting.md#1112-a-feature-does-not-appear).

**What the column does not do:**

- It keeps no cookies and no history once Babble closes.
- It does not ask for your location, your camera or your microphone.
- It does not download files: a download is handed over to your browser.
- It does not run JavaScript unless you turn `JavaScript on Web Pages` on — and many sites do not work without it.

The `Open in Browser` item in the column's menu takes the page you are reading to the browser.

**Column shortcuts:** **Ctrl+R** and **F5** reload, **Alt+Left** and **Alt+Right** navigate, and **Escape** or **Ctrl+W** hide the column.

## 7.2 How links open

Babble distinguishes three kinds of link:

| Type | Where it opens |
|---|---|
| Post or profile link, on any server | **Inside Babble.** The thread or the profile is opened in the window. |
| Any other web page | In the browser, or in the side column, if you turned `Web Links Beside the Timeline` on. |
| Link to a Bluesky server (a bsky.app post, or a post arriving through the *Bridgy Fed* bridge) | **Inside Babble**, if `Bluesky Posts` is on. When it is off, it opens in the browser. |

The `Remote Links in Babble` preference decides whether post and profile links from other servers open inside the application. To send a link to the browser whatever happens, use **Ctrl+click** or the middle button.

The `Tracking Parameter Removal` preference removes tracking parameters from links before you click them. It is on by default, and it **can break some links** — which is why the description warns about it.

## 7.3 Clipboard and drag and drop

Babble takes part in both of GNOME's transfer gestures:

- **Paste** — **Ctrl+V** in the composer pastes copied files and images. A single image becomes a PNG named `Pasted image`.
- **Drag** — you can drag files into the composer, and drag **posts, images, videos and addresses** out to another application. When you drag the image of a post, what goes along is the file and its address. When you drag your own post, what goes is the address.

The `Copied Link Suggestions` preference makes Babble look at the clipboard when it gains focus, and offer to open the post or the profile you just copied. The bar that appears says `Open copied post?`, `Open copied profile?`, `Open copied hashtag?` or `Open copied collection?`, with the `_Open` button.

## 7.4 Opening posts and links from other servers

Babble registers itself as the handler for:

| Scheme | What it is |
|---|---|
| `babble://` | The return of the OAuth authorization, when the browser cannot come back through `127.0.0.1`. |
| `web+ap://` | Federated object (ActivityPub) links. |
| `web+mastodon://` | Links in the Mastodon URL format. |

A link of this kind, coming from another application, opens in Babble instead of the browser.

**One caveat about `babble://`:** for the system to hand this scheme to Babble, it has to be registered as the handler. If you use the Flatpak, the command below has to be run **outside** the sandbox, once per machine:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

Without this registration, the browser says "Unsupported location specified" and Babble never receives the response. **This does not affect normal login**, which uses the listener on `127.0.0.1` and depends on no registration at all. And in any case, the `Enter Authorization Code…` button solves login without either path.

## 7.5 Screenshot

The `Take Screenshot` button, in the composer, uses the XDG screenshot portal. The image is copied to the cache and attached.

The button **only appears** when the portal exists. On a system without the portal, the button's absence is the information — nothing breaks.

## 7.6 Spell checking

The composer checks spelling with `libspelling`, over a buffer of the `GtkSourceView`. Misspelled words are underlined, and the right button shows the suggestions.

The checker follows the language chosen in `Post Language`. If no dictionary is installed for that language, the checking **switches itself off** — and no warning appears, because there is simply nothing to suggest.

## 7.7 System state

Babble watches what GNOME reports and changes its behaviour accordingly:

| System state | What changes |
|---|---|
| **No network** | A `You’re offline` bar appears at the top. The application keeps trying to reconnect and the bar disappears on its own when the network comes back. |
| **Power saving** | Polling becomes less frequent and GIFs stop playing on their own. |
| **Metered connection (mobile data)** | The media viewer waits for you to ask it to load video and audio. |
| **Reduced motion** | GIF autoplay stops. |
| **Animations off** | GIF autoplay stops. |

`Catch Up` takes this into account as well: under power saving, it changes the polling interval.

## 7.8 The time format

Times follow your GNOME preference of 12 or 24 hours, read through the Settings portal. There is no adjustment inside Babble.

## 7.9 System notifications

Desktop notifications carry action buttons: `Favorite`, `Boost` and `Follow Back`. You act on the post without opening the window.

Notifications appear when the Babble window is not focused. When it is focused, they stay inside the application.

When `Background Mode` is on, the GNOME quick settings panel shows how many mentions arrived while you were away.

## 7.10 Launcher actions

Right-click the Babble icon in the GNOME panel and `New Post` and `Notifications` appear, opening the application straight into the requested action.

## 7.11 GNOME search

Babble is a **search provider**: it appears in the Activities overview search, and you can look for accounts, hashtags and posts without opening the window.

To enable it, switch Babble on in **System Settings → Search → Search Providers**.

## 7.12 Running in the background

**Objective:** keep receiving notifications with the window closed.

**Pre-conditions:** the `Background Mode` preference, in Preferences → Notifications, is on; the XDG background portal exists.

1. Close the Babble window. The application keeps running.
2. The Babble icon in the panel is still there, and the quick settings panel shows the mentions that arrived.
3. Click the icon to bring the window back.

**Result:** the application keeps running with no window, the Babble icon stays in the panel, and notifications keep arriving. The GNOME quick settings panel shows how many mentions arrived while you were away.

**If it goes wrong:** if the Babble process ends together with the window, the background portal is not available — the preference has no effect and the application exits with the window. See [11.12](11-troubleshooting.md#1112-a-feature-does-not-appear).

**What it needs:** the background portal. Without it, the preference has no effect and the application exits with the window.

## 7.13 Global quick post shortcut

The `Quick Post Shortcut` preference, in Preferences → Notifications, registers a **system-wide** shortcut that opens the composer from any application. The default is **Super+Shift+M**, and the shortcut can be changed in the GNOME settings.

The preference's button only appears when the XDG global shortcuts portal exists.

## 7.14 Accessibility

- Screen readers announce new batches of posts, and Babble uses accessible descriptions on every media button, in notifications and in the viewer.
- While a post is being sent, the system neither suspends nor locks the session.
- The web column is a `WebKitWebView` with scripts off by default, which reduces the attack surface of third-party pages.

## 7.15 What needs an installation

Three features only work from an **installed build** (`meson install` or Flatpak), because GNOME finds them through the installed files — the `.desktop` file, the D-Bus service and the search provider:

- D-Bus activation (the application starts with no window and opens when the system asks);
- the Activities overview search provider;
- the launcher actions.

The portal-backed features — screenshot, background, global shortcut — only need the matching portal to exist. **A missing portal hides the feature, without breaking anything.**

Everything else works when running straight from the source tree.
