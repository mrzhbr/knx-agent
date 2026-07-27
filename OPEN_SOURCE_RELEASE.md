# Public Release Checklist

The public release must start from a clean, sanitized root commit. Deleting a
private topology or deployment document in a later commit does not remove it from
Git history.

## Why the public history begins here

KNX Agent is the generalized, open-source edition of a privately developed and
actively running home-automation project. The private development repository
contains deployment-specific history, including household topology, device
identifiers, operational configuration, and other details that do not belong in
a public project.

The initial public release therefore starts from a new, independently reviewed
root commit containing only the sanitized, reusable implementation. The short
public history does **not** mean the software was developed in a single commit;
it marks the deliberate boundary between the private installation and its
public OSS edition. Normal public development history continues from that root.

The private repository maintains a `public-main` release branch for this
purpose. It contains only the reviewed publishable tree and no parent commit
from the private development history.

Before creating or updating the public remote:

1. Check out `public-main` in a fresh clone or worktree.
2. Confirm `git rev-list --count HEAD` reports `1` for the initial publication.
3. Run the full CI commands from `README.md`.
4. Run Gitleaks against the clean checkout.
5. Search for deployment addresses, hostnames, Matrix IDs, coordinates, camera
   URLs, room topology, runtime data, and credentials.
6. Push **only** `public-main` to the empty public remote's `main` branch:
   `git push <public-remote> public-main:main`.

Never use `git push --all`, `git push --mirror`, or publish private local refs,
reflogs, bundles, stashes, or ignored deployment files.
