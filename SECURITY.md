# Security Policy

## Reporting a vulnerability

**Do not open a public issue or discussion, and do not email a vulnerability report to the general migrations address.**

Report privately through GitHub's private vulnerability reporting on this repository: **Security > Report a vulnerability**. This creates a private advisory visible only to the maintainers.

Please include the affected version or commit, what an attacker could achieve, and the steps to reproduce.

We will acknowledge the report and keep you updated until it is resolved.

## What this tool handles

This script reads from Xray Cloud and Jira and writes into Qase. Understanding what it touches will help you judge whether something is a vulnerability.

**Credentials.** Read from `config.json` or from the environment:

| Credential | Needs | Used for |
|---|---|---|
| Xray Cloud client id + secret | Read | Everything read out of Xray via GraphQL |
| Jira email + API token | Read, optional | Attachment downloads and resolving issue keys |
| Qase API token | Read and write | Everything written into Qase |

They are held in memory for the duration of the run. **No credential value is written to a log file or printed at any level.** Where the code reports on credentials it logs only whether one is present, never the value.

**Every credential is host-scoped.** The Xray credentials are only ever sent to `xray.cloud.getxray.app`, the Jira credentials only to your `jira.url`, and the Qase token only to Qase. No request crosses between them.

**Source systems are read-only.** Every call against Xray and Jira is a read. The migration has no code path that writes, updates or deletes anything in either, so a failed or repeated run cannot damage your source data.

**The cache holds customer data.** This is a staged pipeline: `extract` writes raw Xray and Jira responses to `cache/<run>/raw_data/`, and `transform` writes mapped payloads to `cache/<run>/transformed/`. Both are verbatim customer content: test definitions, issue keys, account ids, attachment bytes. That is what makes re-running `load` cheap, and it is also a data-at-rest liability.

- `cache/` is gitignored and must never be committed
- delete it when a migration is signed off, and between customers
- a kept cache faithfully replays the *old* source data on a re-run

**Customer data on disk.** Three directories hold it after a run:

- `cache/` raw and transformed source data, including downloaded attachments
- `logs/` can contain test content and API error bodies
- `stats/` per-run counts

All are gitignored. None is needed once a migration is signed off.

## Handling your own credentials

- **Prefer the environment over the file.** `QASE_API_TOKEN`, `XRAY_CLIENT_SECRET` and `JIRA_API_TOKEN` override `config.json` and take precedence, so a config file you paste into a support ticket carries no secrets.
- Generate the Xray pair in Jira under `Apps > Xray > API Keys`, scoped to a user who can see only the projects being migrated.
- The Jira token inherits its user's permissions. Read access is sufficient.
- Revoke every credential used for a migration once it is finished.
- Never commit `config.json`. It is gitignored, but a file added under a different name will not be.
- **Delete `config.json`, `cache/`, `logs/` and `stats/` when the migration is signed off.**

## If a credential is exposed

Revoke it first, then clean up. A token removed from a file but not revoked is still live, and a commit deleted from a public repository stays readable by hash.
