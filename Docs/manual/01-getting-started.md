# 1 — Getting started

> **Document:** `BABBLE-MAN-001` — chapter 1 — v1.0 — approved
> **Software described:** Babble 0.3.0 · **Date:** 2026-11-11 · **Parent:** [The Babble User Manual](README.md)

This chapter takes you from opening the application to reading your feed: signing in to your account, understanding what the application asks the server for, managing several accounts, and signing out.

## 1.1 How sign-in works

Babble never asks for your password. It uses the OAuth authorization flow — the same one your browser uses when it asks for access to a site.

It happens in this order:

1. You type your server's address and click `Continue`.
2. The browser opens your **own server's authorization page** — not Babble's. There you type your password, on the server, exactly as if you were signing in to it.
3. The server shows what is being requested. In a normal sign-in, that is two scopes: `read` (read) and `write` (publish).
4. The server returns a **token** to the application. It is that token, not your password, that Babble stores.
5. The token goes to the **system keyring** (GNOME Keyring, KeePassXC, or equivalent).

The token works like a key: anyone who obtains it can act as you. That is why it stays in the keyring, which asks for your system password before it opens.

**Why `read write` and nothing more.** Every scope requested shows up on the server's consent screen, one by one. If Babble asked for moderation scopes as well, they would show up there too and be accepted along with the rest. That is why the administrative scopes (`admin:read admin:write`) are requested **separately**, only when you open the `Admin Dashboard`, and only if your account's role on the server allows it.

**What the token gives access to.** `read` covers listing and viewing posts, profiles, notifications, conversations, lists, filters, and edit history. `write` covers posting, editing, deleting, replying, boosting, favoriting, bookmarking, following, muting, and blocking. There has been no separate `follow` scope since Mastodon 3.5: following is part of `read`/`write`.

### The browser callback

When the server returns the token, it redirects the browser to a local address on your own computer (`http://127.0.0.1:<port>/callback`). Babble opens a listener on that port before it starts, so the browser hands the token straight to the application, without any intermediate server. The browser tab tells you that it is safe to close it.

The flow uses **PKCE** (RFC 7636) and a fresh `state` on every attempt, so that an old or tampered response is never accepted.

If for some reason the browser cannot come back — the most common case is the computer failing to open the port — Babble falls back to a `babble://` redirect, which depends on the system being configured to open that scheme with Babble (see [7.4](07-desktop-integration.md#74-opening-posts-and-links-from-other-servers)). As a third resort, and the safest one when there is a firewall, the `Enter Authorization Code…` button lets you paste by hand the code the server displayed.

## 1.2 Signing in for the first time

**Objective:** sign in to your account and see the `Home` timeline.

**Pre-conditions:** you have an account on a Mastodon server; the system keyring is running; you have a network connection.

1. Open Babble. The welcome window appears, with the title `Welcome to Babble` and the text `Log in with your Mastodon account`.
2. Click the `Server Address` field and type your server's address, without `https://` and without a trailing slash — for example, `mastodon.social`. If you prefer, click one of the popular server chips (`Other Popular Servers`) to fill the field in.
3. Click `Continue`.
4. The browser opens your server's authorization page. Sign in with your account and approve the request.
5. The browser returns to Babble on its own and the tab shows that the sign-in is complete. Close the tab.
6. The window now shows the `Home` timeline.

**Result:** the account button at the top of the sidebar shows your avatar, your name, and your `@account`. The `Home` timeline loads.

**If it goes wrong:**

- *The browser does not come back.* Use `Enter Authorization Code…` in the welcome window and paste the code the server displayed. See [11.2](11-troubleshooting.md#112-the-login-does-not-proceed).
- *The server refuses the request.* Babble records the application registration failure on the server and reports it. Very restrictive servers may refuse; in that case, another client on the same server works.
- *The application says it could not find the keyring.* See [11.3](11-troubleshooting.md#113-the-keyring-does-not-keep-the-login).
- *The window stays on `Welcome to Babble` even after the browser tab is closed.* You have not authorized yet: go back to the browser and approve the request, or use the code.

## 1.3 Adding, switching and removing accounts

Babble stores as many accounts as you like, from different servers. They all live in the same keyring, and each one keeps its own drafts, its own Catch Up snapshots, and its own preferences separate.

### Adding another account

**Objective:** have two Babble accounts open at the same time.

**Pre-conditions:** you have already signed in to at least one account.

1. Click the account button at the top of the sidebar (the one showing your avatar).
2. Click `Add Account…`.
3. Repeat the steps in [1.2](#12-signing-in-for-the-first-time).

**Result:** the new account appears in the list. Babble stays on the previous account until you switch.

**If it goes wrong:** if the new account does not appear in the list after you authorize, the keyring may not be writing — see [11.3](11-troubleshooting.md#113-the-keyring-does-not-keep-the-login). If the server refuses the request, see [11.2](11-troubleshooting.md#112-the-login-does-not-proceed).

### Switching accounts

**Objective:** start reading and posting as another account.

**Pre-conditions:** there is more than one stored account.

1. Click the account button at the top of the sidebar.
2. Click the account you want in the list.

**Result:** the interface reloads for the chosen account, and the check mark next to it becomes the current one. Switching also switches the server: what you see in `Local`, for example, becomes the public timeline of the other server.

**If it goes wrong:** if the switch does not happen, the server may be rate-limiting requests — try again in a moment. If the account has disappeared from the list, it was removed from the device: sign in again from the welcome window. See [11.14](11-troubleshooting.md#1114-i-can-no-longer-use-an-account).

### Signing out of an account

**Objective:** end an account's session and remove its credentials from this computer.

**Pre-conditions:** there is at least one stored account.

1. Click the account button at the top of the sidebar.
2. Next to the account you want to remove, click the trash button. Its tooltip is `Remove Account`.
3. Confirm in `Remove Account?`. The warning says: *"The login for @account will be removed from this device."*

**Result:** Babble reads the account's token, deletes the keyring entry, and removes the account from the list — all locally, without waiting for the network. It then asks the server to revoke the token and discards the application's credentials on the server. The other stored accounts are not affected.

**If it goes wrong:** if the revocation on the server fails, the application logs the warning to the system log and proceeds with the local removal. The account leaves Babble either way; the server may, in that case, keep considering the token valid until the revocation arrives. To see what the application logged, see [11.6](11-troubleshooting.md#116-the-system-log).

## 1.4 Opening the help

Babble ships with its own help, installed alongside the application, which opens with `F1` or from the `Help` item in the main menu. It covers the essentials in summary form and is available in English.

The help is available in **English** and in **Portuguese (pt-BR)**: the help reader picks the language folder based on the system language, so the page appears in the language of your environment. The Portuguese pages live in `data/help/pt_BR/` and are installed next to the English ones, in `data/help/C/`.

This manual is the complete reference: it explains the *why* behind each behaviour, lists limits and error messages, and records the decisions the application makes in ambiguous situations.

## 1.5 Quick reference for first use

| Action | How |
|---|---|
| Open the composer | `New Post`, or **Ctrl+N** |
| Reload the current page | **Ctrl+R** or **F5** |
| Change the sidebar mode | `Sidebar` button, at the bottom of the sidebar |
| Open Preferences | **Ctrl+**, |
| Open the shortcuts list | **Ctrl+?** |
| See the shortcuts | `Keyboard Shortcuts`, in the main menu |
| Go to `Home` | **Ctrl+1** |
| Go to `Notifications` | **Ctrl+2** or **Ctrl+Shift+N** |
| Go to `Search` | **Ctrl+F** or **Ctrl+K** |
| Close the window | **Ctrl+W** |
| Quit the application | **Ctrl+Q** |

The full list is in [chapter 10](10-reference.md).
