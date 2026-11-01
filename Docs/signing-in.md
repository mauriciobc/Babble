# Signing in

Type your server's address, approve Babble in your browser, and you're in.
Babble only asks for `read write`, and your token goes into the system
keyring, not a file.

## How the login works

During login Babble listens on a `127.0.0.1` port and registers
`http://127.0.0.1:<port>/callback` as the OAuth redirect, so the browser hands
the authorization straight back. The tab then tells you it's safe to close.
The flow uses PKCE (RFC 7636) and a fresh `state` on every attempt.

A denied authorization ends the attempt without saving credentials; an invalid
state or redirect leaves the legitimate attempt available. Invalid JSON, missing
tokens, and invalid account identities are reported as login failures, not adopted
as accounts.

## When no port can be opened

Babble falls back to a `babble://` redirect. That one needs a single command,
run once per machine on the host:

```sh
xdg-mime default io.github.mauriciobc.Babble.desktop x-scheme-handler/babble
```

The desktop entry already advertises `x-scheme-handler/babble`, but GLib only
resolves a URI scheme that's also listed in `~/.config/mimeapps.list`, which
is what `xdg-mime default` writes. Without it, the browser's handoff fails
with "Unsupported location specified" and Babble never hears back.

Flatpak cannot write the host's `mimeapps.list` from inside the sandbox, so
this has to run outside it. It applies to `meson install` too.

**"Enter Authorization Code…" on the login page works without either
redirect** — paste the code the browser shows you.

## Scopes

The ordinary login asks for `read write` and nothing else. Every requested
scope is listed on the instance's consent screen, so nothing extra goes in
there by accident.

The admin scopes (`admin:read admin:write`) are requested only by the admin
dashboard, and only once your account's role says it could use them — that
authorization runs separately, on top of the session you are already signed
in to.
The browser must authorize the same account and server already signed in to
Babble. Switching accounts during authorization prevents the old response from
replacing the new session's credentials.

## Removing an account

Local sign-out does not wait for best-effort server revocation or keyring cleanup.
Babble attempts to delete both the account token and its OAuth application
credentials, scoped to that account and server. A keyring failure reports one
warning after both deletions answer; it does not restore the removed account or
sign out another session.
