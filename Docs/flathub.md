# Flathub policy

`build-aux/flatpak/io.github.mauriciobc.Babble.json` is a **local development
manifest only**. Babble is not on Flathub.

If that ever changes, these rules apply and are not negotiable:

- A manifest submitted to Flathub must be written by a human with no AI
  assistance. Disclosure does not exempt it.
- Disclose AI-generated application code or documentation in any Flathub
  submission.
- Do not use an AI agent to open or automate Flathub pull requests, or to
  write their commit messages, descriptions, review comments or replies.

These come from the Flathub requirements themselves, not from preference.
CI builds the local manifest on every push — that is a rot check on a
developer manifest, and carries no implication about a future submission.
