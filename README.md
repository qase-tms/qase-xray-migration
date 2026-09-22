# Xray Cloud to Qase

Migrates projects, folders, test cases, executions, runs and attachments from **Xray Cloud** into **Qase**.

This migration runs as a three-stage pipeline (`extract` → `transform` → `load`) rather than in one pass. That is deliberate and it is the most useful thing about it: a failed load re-runs from cached extract without touching Xray again. See section 10.

---

## 1. What this migrates

**Source:** Xray Cloud, through its GraphQL API, plus Jira REST for attachment downloads and issue keys.

**Supported**

- Xray **Cloud** only, authenticated with a client id and secret pair
- Jira Cloud, for attachments and issue-key resolution
- Any Qase workspace: public cloud (`qase.io`) or a dedicated cluster

**Not supported**

- **Xray Server and Data Center.** Different API, not implemented.
- **Jira OAuth for Jira Cloud.** The `jira.oauth_client_id` and `jira.oauth_client_secret` keys exist but are not functional against Jira Cloud. Use `jira.email` plus `jira.api_token`.

---

## 2. Coverage table

| Xray | Qase | Status | Notes |
|---|---|---|---|
| Project | Project | Migrated | Code derived from the Jira key, or set with `projects.mapping` |
| Folder | Suite | Migrated | Repository folder tree, nested |
| Test | Test case | Migrated | Summary, description, steps, priority where present |
| Test step | Step | Migrated | Action, data and expected result |
| Gherkin scenario | Steps | Partial | Parsed into line-based steps where possible. `scenarioType`, feature files and tables are kept in the raw cache but not modelled |
| Test execution | Test run | Migrated | |
| Test run | Result | Migrated | Status and duration. See limitations on timestamps |
| Attachment | Attachment | Migrated | Downloaded via Jira REST; needs `jira.email` and `jira.api_token` |
| Test plan, test set, board | Nothing | Not migrated | |
| Precondition | Nothing | Not migrated | Not fully queried or mapped |
| Requirement / coverage | Nothing | Not migrated | Qase has no requirement entity |
| Test version, parameter, iteration, dataset | Nothing | Not migrated | |
| Custom field on test, execution or step | Nothing | Not migrated | |
| User | Nothing | Not migrated | Assignee and executed-by are not applied |
| Jira component, epic, fix version | Nothing | Not migrated | Priority **is** mapped when present |
| Defect | Nothing | Not migrated | GraphQL often returns ids without Jira keys |

---

## 3. Known limitations

Read this section before promising anything to a stakeholder.

**Historical result timestamps are not preserved.** Qase validates result execution times against the run's start, which is roughly "now" when the run was just created. Xray exports real historical `startedOn` and `finishedOn`, which are always in the past against that anchor and produce a `422`. Duration and status are preserved; absolute timestamps are omitted.

**A second run duplicates data.** The load phase is create-only. Migrate into an empty Qase project, or delete and start again.

**Users are not migrated.** Every migrated case and result is attributed to the Qase token's own user. Assignee and executed-by from Xray are dropped.

**Attachments need Jira credentials.** Xray returns attachment ids, and the bytes come from Jira REST. Without `jira.email` and `jira.api_token` attachments are skipped.

**Test plans and test sets are not migrated.** If an Xray instance is organised primarily by test plan, that organisation does not survive. Folders do.

**This script has not been run against a live Xray Cloud instance by us.** Everything verifiable without one is covered by the unit tests in `tests/`, which check config handling, host derivation, project selection, retry semantics, logging and the end-of-run report:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Two of those tests are structural: one asserts `config.example.json` and the keys the code reads match in **both** directions, and one asserts every write method on the Qase service is overridden by the dry-run service. Both fail automatically if someone adds a key or a write later. The mapping of Xray payloads to Qase entities is **not** covered, because that needs real responses. Use `--dry-run` first and migrate into an empty project.

---

## 4. Prerequisites

**Python 3.11 or newer.** 3.10 reaches end of life in October 2026 and 3.9 already has. `cli.py` and `preflight.py` both refuse to run on anything older rather than failing partway through.

### Xray Cloud access

1. In Jira, open `Apps > Xray > API Keys`.
2. Press **Create API Key** for the user whose permissions the migration should inherit. That user needs to see every project you intend to migrate.
3. Copy the **Client ID** and **Client Secret**. The secret is shown once.
4. Confirm they work before going any further:

   ```bash
   curl -s -X POST "https://xray.cloud.getxray.app/api/v2/authenticate" \
     -H "Content-Type: application/json" \
     -d '{"client_id":"<id>","client_secret":"<secret>"}'
   ```

   A quoted JWT means the pair is good. A `401` means it is not.

5. Put them in `config.json` as `xray.client_id` and `xray.client_secret`. `XRAY_CLIENT_SECRET` overrides the secret.

### Jira access (needed for attachments)

1. Open `https://id.atlassian.com/manage-profile/security/api-tokens`.
2. Press **Create API token**, name it, copy the value.
3. Put your Atlassian account email in `jira.email` and the token in `jira.api_token`, or export it as `JIRA_API_TOKEN`.
4. Set `jira.url` to your site, for example `https://acme.atlassian.net`.

Read access is sufficient. Leave both blank and attachments are skipped; everything else still migrates.

### Qase access

1. Open `https://app.qase.io/user/api/token`.
2. Press **Create new token**, give it **read and write** access to projects, test cases, test runs and attachments.
3. Put it in `config.json` as `qase.api_token`, or export it as `QASE_API_TOKEN`.

The Qase user who owns the token needs a role that can create projects, or you must pre-create them and map them with `projects.mapping`.

### Nothing needs to be created up front

This migration writes no source ids into Qase custom fields, so there are no fields to create before running.

---

## 5. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

Then edit `config.json`.

---

## 6. Configure

One file, `config.json`. Every key below is read by the code, and every key the code reads is listed here.

### `qase`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `api_token` | Yes | | Qase API token. `QASE_API_TOKEN` overrides it |
| `host` | No | `qase.io` | Anything other than `qase.io` is treated as a dedicated cluster and the API URL derives from it |
| `ssl` | No | `true` | Set to `false` only for an HTTP test instance |

### `xray`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `client_id` | Yes | | Xray Cloud client id |
| `client_secret` | Yes | | Xray Cloud client secret. `XRAY_CLIENT_SECRET` overrides it |

### `jira`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `url` | Yes | | Your Jira site, e.g. `https://acme.atlassian.net` |
| `email` | No | `""` | Atlassian account email. Needed for attachments |
| `api_token` | No | `""` | Jira API token. `JIRA_API_TOKEN` overrides it |
| `oauth_client_id` | No | `""` | **Not functional for Jira Cloud.** Kept for a future Server path |
| `oauth_client_secret` | No | `""` | Same |

### `projects`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `import_all` | No | `false` | Migrate every project the Xray credentials can see |
| `import` | No | `[]` | Jira project keys to migrate. Case-insensitive |
| `exclude` | No | `[]` | Keys to skip. Always wins over `import` and `import_all` |
| `mapping` | No | `{}` | Jira project key to a specific Qase project code, e.g. `{"PAY": "PAYMENTS"}` |

### `cases`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `preserve_ids` | No | `false` | Send the Xray test id as the Qase case id |

### `cache`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `dir` | No | `./cache` | Where extract and transform write. **Holds raw customer data**, see SECURITY.md |

### `logging`

| Key | Required | Default | Meaning |
|---|---|---|---|
| `level` | No | `info` | `error`, `warn`, `info`, `verbose` or `debug`. Each includes the ones above |
| `write_to_file` | No | `true` | Also write to `logs/` |
| `dir` | No | `./logs` | Where the log file goes |

`error` and `warn` always reach the console whatever the level, so a run that quietly skipped data cannot look successful in the terminal. `--log-level` overrides this for a single run.

### `prefix`

Optional string prepended to the log filename, for telling runs apart.

---

## 7. Validate

Always run this before migrating:

```bash
python preflight.py
```

It checks the config, rejects placeholder values, authenticates against Xray Cloud, checks Jira credentials if set, and verifies the Qase token.

A green run looks like this:

```
- Config -
  ✅ Config file config.json: parses OK
  ✅ qase.api_token (Qase API token)
  ✅ xray.client_id (Xray Cloud client id)
  ✅ xray.client_secret (Xray Cloud client secret)
  ✅ jira.url (Jira site URL)
  ✅ projects.import: 2 project key(s): ['PAY', 'PLAT']

- Xray Cloud -
  ✅ Xray authentication: token issued

- Jira -
  ✅ Jira auth (GET /rest/api/3/myself): you@acme.com

- Qase -
  ✅ Qase auth (GET /v1/project): 12 project(s) in workspace

✅ Preflight passed, ready to run: python cli.py migrate
```

Exit code is `0` when everything passes and `1` when anything fails.

---

## 8. Run

```bash
python cli.py migrate                      # all three phases
python cli.py migrate --dry-run            # read and transform, write nothing
python cli.py extract                      # phase 1 only
python cli.py transform --cache cache/<run>
python cli.py load --cache cache/<run>
python cli.py load --cache cache/<run> --dry-run
```

`--dry-run` runs extraction and transformation in full and logs every write it would make, so unmapped values and missing fields appear in the migration report without anything being created in Qase.

`--log-level` and `--log-file` override the config for one run.

Exit code is `0` on success and `1` on failure.

**Expected duration.** We have no measured figures. Xray's GraphQL API paginates in modest page sizes and attachment downloads dominate anything with files, so budget on extraction rather than on load. The staged pipeline means a slow extract is paid once, not once per attempt.

---

## 9. What good output looks like

> **These samples are constructed from the code, not captured from a real run.** We have no Xray Cloud instance. The format is accurate because it is produced by the logging and report modules in this repo, but the numbers are illustrative.

Each phase logs progress through Python's standard logging, to the console and to `logs/` together:

```
2026-09-21 18:22:01 - extractors.xray_cloud_extractor - INFO - Starting Xray Cloud extraction...
2026-09-21 18:22:04 - extractors.xray_cloud_extractor - INFO - Saved projects (2 items) to cache
2026-09-21 18:22:31 - extractors.xray_cloud_extractor - INFO - Saved test_cases (340 items) to cache
2026-09-21 18:24:02 - transformers.xray_transformer - INFO - Transformation completed successfully!
2026-09-21 18:25:40 - loaders.qase_loader - INFO - Created run 88 for PAY
```

Warnings and errors reach both the console and the log file at any level:

```
2026-09-21 18:25:44 - loaders.qase_loader - WARNING - [PAY][Cases] attachment 41200 skipped, no Jira credentials
2026-09-21 18:25:51 - services.qase_service - ERROR - [Fields] Error creating custom field: Test Environment
```

Then the migration report:

```
------ Migration report: 2 skipped/degraded item(s) ------

  [PAY] · 1 item(s)
    ! [Cases] attachment 41200 skipped, no Jira credentials

  [-] · 1 item(s)
    ✗ [Fields] Error creating custom field: Test Environment

Cache directory: cache/xray_extraction_20260921_182201
Full log: logs/xray_20260921_182201.log
```

On a clean run it says so explicitly rather than printing nothing:

```
------ Migration report: no skipped or degraded items ------
```

**Counts alone can overstate success.** The report is generated from every warning and error the run produced. Given that test plans, test sets, preconditions, custom fields and users are not migrated at all, the counts will look healthy while those are simply absent. Check section 2 against what you expected.

---

## 10. Re-run and resume behaviour

**This is the strongest part of this migration and the reason for the staged pipeline.**

`extract` writes every Xray and Jira response to `cache/<run>/raw_data/`. `transform` reads that and writes `cache/<run>/transformed/`. `load` reads that and writes to Qase. Each phase takes `--cache` so you can re-run any one of them against an existing cache.

That means:

| Failure | What to do |
|---|---|
| Load failed partway | `python cli.py load --cache cache/<run>` again. **Xray is not touched.** |
| Transform produced something wrong | fix, then `transform` and `load` from the same cache |
| Extraction died midway | re-run `extract`. Whole entity types already written are kept; a type interrupted mid-fetch is refetched |

What it does **not** do:

- **The load phase is create-only.** Re-running `load` against a project that already received data duplicates cases, runs and results. Resume protects you from re-reading Xray, not from re-writing Qase.
- **The cache goes stale.** It faithfully replays the Xray of the day you extracted. Re-extract for current data.
- **The cache holds raw customer data.** See SECURITY.md. Delete it when a migration is signed off.

**The safe way to re-run a load is an empty target project.**

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `This migration requires Python 3.11 or newer` | Interpreter too old | Create the virtualenv with Python 3.11+ |
| `Config file not found` | No `config.json` | `cp config.example.json config.json` and fill it in |
| Preflight: `looks like a placeholder` | Example values still in place | Replace every `<...>` value |
| Xray `401` on authenticate | Wrong client id or secret | Regenerate in `Apps > Xray > API Keys` |
| Attachments all skipped | No Jira credentials | Set `jira.email` and `jira.api_token` |
| Results rejected with `422` about time | Historical timestamps against a new run's anchor | Expected. Duration and status are preserved, absolute times are not |
| Qase `403` on project creation | Token is member-level | Use an owner or admin token, or pre-create projects and use `projects.mapping` |
| Bulk case create fails with `422` | A custom field exists in the workspace but is not scoped to this project | Custom fields are workspace-global in Qase. Add the target project to the field's scope |
| A re-run created duplicates | Load is create-only | Migrate into an empty project |
| `logs/` looks empty on an old run | Fixed in this version | Module logs previously never reached the file. Re-run to get a complete log |

For anything else, run at `verbose` or `debug`:

```json
"logging": { "level": "verbose" }
```

No credential value is ever written to the log at any level.

---

## 12. Getting help

Email **migrations@qase.io**.

Include:

- The version, which is the first line of the log file and the last line the run prints
- Which phase failed: `extract`, `transform` or `load`
- Your `config.json` **with every token removed**
- The tail of the log file from `logs/`
- The migration report as printed at the end of the run

The log and cache contain test content and account identifiers, so send them only over a channel you are comfortable with. Delete `cache/`, `logs/`, `stats/` and `config.json` once the migration is signed off.

For a suspected security issue, do not email this address. See [SECURITY.md](SECURITY.md).

Every release is listed in [CHANGELOG.md](CHANGELOG.md).
