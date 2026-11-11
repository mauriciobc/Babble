# 11 — Troubleshooting

> **Document:** `BABBLE-MAN-001` — chapter 11 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

Each item below carries the **symptom** (what you see), the **likely cause** and the **action** that fixes it. If the symptom is not here, [chapter 10](10-reference.md) has the list of messages by area.

## 11.1 The application does not open

| Symptom | Likely cause | Action |
|---|---|---|
| The application does not start and the window does not appear | It is already running: Babble is single-instance | Look for the icon in the GNOME panel and click it. If it is hidden, use the application search. |
| The application does not start and the terminal reports `Settings schema … is not installed` | The GSettings keys are not installed | Use an installed build (`meson install`) or point `GSETTINGS_SCHEMA_DIR` at the `data` folder of the build. |
| The window opens and closes immediately | A failure during GTK startup | Run the application in a terminal to see the error message. |
| The application does not appear in the Activities overview, in the applications menu or in the search provider | The `.desktop` file, the D-Bus service and the search provider are **installed** files | Install the build. These three features do not work from the source tree. |

## 11.2 The login does not proceed

| Symptom | Likely cause | Action |
|---|---|---|
| The browser opens the server page but does not come back to Babble | The browser could not reach the listener on `127.0.0.1` | Use the `Enter Authorization Code…` button on the welcome screen and paste the code the server displayed. |
| The browser says "Unsupported location specified" | The `babble://` scheme is not registered on the system | Register the scheme, once: `xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble`. Under Flatpak, run it outside the sandbox. |
| The window stays at `Welcome to Babble` after you authorized | The authorization was denied, or it was authorized on a different account | Try again and approve the request on the same account and the same server already connected to Babble. |
| The server refuses the authorization request | The application registration was refused by the server | Try another client from the same server. Babble records the registration failure in the system log. |
| The login works, but the account is not saved | The keyring is not reachable | See [11.3](#113-the-keyring-does-not-keep-the-login). |

**Tip:** `Enter Authorization Code…` works **without either** of the two redirect paths. If the login is giving you trouble, use it.

## 11.3 The keyring does not keep the login

| Symptom | Likely cause | Action |
|---|---|---|
| The application says it found no keyring service | No keyring service is running | Turn on GNOME Keyring (or KeePassXC, or another compatible service) and **restart Babble**. |
| The login is asked for every time the application opens | The keyring is running, but the application cannot talk to it | Check that the service is unlocked. Under Flatpak, the application needs the permission to talk to `org.freedesktop.secrets`. |
| The notice says the service exists, but the account does not come back | The account entry was deleted from the keyring from outside | Sign in again from the welcome screen. Babble forgets the account instead of trying forever. |

Babble keeps every account in the system keyring. What it stores there is a **token**, not your password: revoking the access means revoking the token on the server.

## 11.4 The timelines do not update

| Symptom | Likely cause | Action |
|---|---|---|
| Posts do not arrive on their own | Live updates are off | Preferences → Timelines → `Live Timelines`. |
| The `Home` updates, but `Local` and `Federated` do not | Live updates of the public timelines are off | Preferences → Timelines → `Live Public Timelines`. It depends on `Live Timelines` and **uses more memory** on very busy timelines. |
| Nothing updates and the server seems slow | The server limited the rate of requests | Wait for the limit to pass. Automatic refresh pauses while the server is limiting. |
| Posts arrive late after a period offline | The live stream reconnected and is catching up on what it missed | That is the expected behaviour. If it persists, reload the page. |

## 11.5 The `You’re offline` bar

| Symptom | Likely cause | Action |
|---|---|---|
| The `You’re offline` bar appears | The application lost the network | Wait. The bar disappears on its own when the network comes back, and the live stream reconnects. |
| The bar does not disappear | The network came back, but the stream did not reconnect | Close and open the window, or restart the application. |
| The bar appears even with a network | A proxy or firewall blocks the long-lived connection | Check the Flatpak network permissions, if that is the case. |

## 11.6 The system log

Babble writes to the system log the messages that explain the behaviour of the login and of the accounts. To read them:

```sh
journalctl --user -g 'accounts:' -f
```

The account lines carry the identifier of the Babble process (`BABBLE_PID`), which lets you follow one specific application. The kernel log and the user log are separate; if the problem is about the network or a portal, `journalctl --user` shows the application's part, and `journalctl -b` shows the system's.

This log is the best source when a plugin fails: the `Plugins` page shows the summary, and the log has the detail.

## 11.7 Media does not load

| Symptom | Likely cause | Action |
|---|---|---|
| The viewer says `Media Not Stored by Your Server` | Your server has no copy, and you refused to load it from the origin server | Use `Open in Browser`, or change `Media From Other Servers` to `Always Load` or `Ask Each Time`. |
| A toast says `Loading media from other servers is turned off` | The setting is `Never Load` | Change the setting, if you want to load. |
| The video shows `Load Video` instead of playing | The connection is marked as metered, or the setting is `Never` | Click `Load Video`, or change `Load Media Automatically`. |
| The GIF does not play on its own | Reduced motion, animations off, power saving, or `Autoplay Animated Media` off | Check the four conditions in [5.8](05-media.md#58-autoplay-and-loading). |
| The GIF does not play inside the timeline | `Play Animated GIFs in Timelines` is off | Turn the preference on. Only the most visible GIF plays, one at a time. |
| The image is cropped | The default scaling crops the media to fill the space | Turn on `Letterboxed Media`. |
| The viewer says `Playback is not available on this system` | The system has no video decoders | Open the file in the browser. |

## 11.8 A post disappeared

| Symptom | Likely cause | Action |
|---|---|---|
| The post does not appear, and in its place there is a `Filtered: {name}` bar | A filter on your server matched it | Click `Show Anyway`, or review the filter in Preferences → Reading → `Manage Filters`. |
| The post neither appears nor is there any bar | The filter is of the `Hide` kind, and it removes the post without warning | The filter is on the server; adjust it there, or ask whoever administers the server. |
| The media is covered, but the text appears | A filter of the `Blur` kind matched it | That is the behaviour of `Blur`. |
| The boosts disappeared from the `Home` | `Hidden Boosts on Home` is on | Turn the preference off. |
| Replies from accounts you do not follow disappeared | `Hidden Replies From Non-Follows` is on | Turn the preference off. |

## 11.9 The Catch Up is incomplete

| Symptom | Likely cause | Action |
|---|---|---|
| The bar says `Covers {interval} ({n} posts)` | The server delivers at most 800 posts of the `Home`, whatever the window asked for | Narrow the time window, or accept the partial coverage. |
| The bar says `Only {n} post in {interval}` | There was little activity in the period | Use the `Catch up on 24 hours` button. |
| The notice mentions a request limit | The server limited the rate, and the scan was interrupted | Wait the indicated minutes and try again. |
| The snapshot does not appear next time | It could not be written | The `This catch-up could not be saved` notice appears; do the catch-up again with a smaller window. |

## 11.10 A plugin stopped working

| Symptom | Likely cause | Action |
|---|---|---|
| The plugin appears with a warning sign under its name | It raised an exception | Babble **turns off** the plugin and says that it did so. The reason is in the system log. |
| The plugin does not appear in the list at all | It could not be loaded | The reason is in the log as well. It is tried again the next time the application starts. |
| The plugin vanished after an application update | It belongs to a different version of Babble | Look for the matching version. |
| The `Plugins` page says `Plugins Are Unavailable` | `libpeas 2` is not installed | Install `libpeas 2` and its Python loader. |

After a plugin has been loaded, **restarting the application** is necessary: Python cannot unload a module.

## 11.11 The interface is in English

| Symptom | Likely cause | Action |
|---|---|---|
| The interface is not in Portuguese | The translation is not complete in 0.3.0, or the catalogue is not installed | Preferences → Appearance → `Language` → `Português (Brasil)`, and **restart the application** — the language only takes effect at the next start. |
| Only some labels are in Portuguese | The pt-BR catalogue is incomplete | The missing ones appear in English; that is the expected behaviour in this version. The [label table in the Brazilian Portuguese edition of chapter 10](10-reference.md#105-interface-labels-in-english-and-portuguese) shows what is already translated. |

## 11.12 A feature does not appear

| Symptom | Likely cause | Action |
|---|---|---|
| `Collections` is not in the menu | The server predates Mastodon 4.6 | Nothing to do: the feature does not exist on the server. |
| `Admin Dashboard` is not in the menu | Your account has no administrator role | Ask the server administrator for the role. |
| There are no tabs on someone's profile | The server or the account does not expose that data | It depends on the server and on the choices of the account. |
| There is no `Notification Filtering` in Preferences | The server predates Mastodon 4.3 | Use Babble's own filters. |
| There is no `Default Quote Policy` in Preferences | The server predates Mastodon 4.5 | Quotes do not work on that server either. |
| The `Take Screenshot` button is gone | The XDG screenshot portal does not exist on the system | Install the matching portal. |
| There is no web column beside the timeline | WebKitGTK is not installed, or the preference is off | Check `Web Links Beside the Timeline`. |

Babble hides what the server does not support, instead of showing a button that fails. The version table is in [10.7](10-reference.md#107-limits).

## 11.13 The application uses too much memory

| Symptom | Likely cause | Action |
|---|---|---|
| Memory grows as you scroll a long timeline | That is the behaviour of a long list with images and previews | Close the window and open it again; the pages are recreated. |
| Memory climbs a lot on `Local` and `Federated` | Live updates of these timelines keep a lot alive | Turn off `Live Public Timelines`. |
| Memory rises after opening many videos | The media engines only release once they stop | Close the media viewer; the engine is released when it closes. |
| The animated GIF in the timeline stalls on first use | The first use loads the decoder | That is why `Play Animated GIFs in Timelines` comes off. |

## 11.14 I can no longer use an account

| Symptom | Likely cause | Action |
|---|---|---|
| The account vanished from the list | It was removed from the device | Sign in again from the welcome screen. |
| The server still recognizes the account, but Babble does not | The revocation of the token failed on the server | The notice stays in the system log. Sign in again to get a new token. |
| The account asks for a login every time it opens | The keyring entry was deleted | See [11.3](#113-the-keyring-does-not-keep-the-login). |

## 11.15 When nothing works

Put together, these are the three steps that solve most cases:

1. **Restart the application.** Several problems — a plugin that stopped, a stuck live stream, composers with stale state — are only solved this way, because Python does not unload modules and the network stream does not reconnect on its own.
2. **Restart the keyring.** If the login does not stick, the keyring service has to be restarted and unlocked.
3. **Read the system log.** `journalctl --user -g 'accounts:' -f` shows the decisions the application makes about accounts and sessions, and it is where the reason a plugin failed shows up.
