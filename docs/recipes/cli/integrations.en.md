---
description: "Use to generate integrations and OpenAPI clients, simulate Mercado Pago, operate agents, export the OpenAPI contract and draft a PR description with `tempest pr-prompt`."
---

# Integrations, agents and the OpenAPI contract

Use to generate integrations and clients, simulate Mercado Pago, operate agents, export the OpenAPI contract and write a PR description with AI.

Part of the [CLI](../cli.md) recipe.

## Integrations — `tempest integrations`

```bash
tempest integrations list                    # what ships + the credential state here
tempest integrations verify openpix          # one real authenticated call
tempest integrations verify mercado-pago
tempest integrations verify zap --base-url http://localhost:3000
```

In a project fresh out of `tempest new`, with no credential in the `.env`:

```text
openpix       tempest_fastapi_sdk.integrations.payment.openpix:OpenPixClient
              OPENPIX_APP_ID not set
mercado-pago  tempest_fastapi_sdk.integrations.payment.mercado_pago:MercadoPagoClient
              MERCADOPAGO_ACCESS_TOKEN not set
stripe        tempest_fastapi_sdk.integrations.payment.stripe:StripeClient
              no settings mixin (build the client yourself)
zap           tempest_fastapi_sdk.integrations.messaging.zap:ZapClient
              no settings mixin (build the client yourself)
```

`verify` builds the client with `HTTPClient(**settings.<provider>_kwargs())`
and makes **the cheapest authenticated read** each API offers: OpenPix's
`GET /company` and Mercado Pago's `GET /users/me` — neither moves money nor
creates a record. A bad credential exits 1 carrying the provider's refusal.

!!! note "That endpoint is a hand-kept table — with a guard"
    Choosing "the cheapest call" per provider is a human decision, and
    generated endpoint names change. A test asserts each named method still
    exists on the generated client **and** takes no required argument, so an
    upstream rename breaks a test instead of the terminal of whoever is
    on call.

The two providers the SDK ships no settings mixin for behave differently:

- **zap** needs `--base-url`, because the SDK does not know where your
  instance lives. `verify` calls `health`, which takes no credential, so it
  measures **reachability**, not authorization. Without `--base-url` it
  exits 2; an instance that is down exits 1:

    ```text
    error: zap has no settings mixin, so the CLI does not know where it lives. Pass --base-url.
    error: zap refused the call — ConnectTimeout:
    ```

- **stripe** has no `verify`: the API offers no authenticated read that
  neither spends money nor creates a record. The command exits 2, with or
  without `--base-url`:

    ```text
    error: stripe exposes no cheap authenticated read, so there is nothing the CLI can call without spending money or creating a record. Construct the client in your service.
    ```

## Simulated Mercado Pago — `tempest mercado-pago`

```bash
tempest mercado-pago fake-api                # simulated Orders API on 127.0.0.1:8090
tempest mercado-pago simulate-webhook --fake-api http://127.0.0.1:8090 \
    --order-id ORDFAKE… --transition pay     # moves the order and delivers the signed notification
```

`fake-api` serves `FakeMercadoPagoOrdersAPI` (state in memory); the service
points at it with `MERCADOPAGO_BASE_URL=http://127.0.0.1:8090`.
`simulate-webhook` signs the notification with the secret read from
`--secret-env` (default `MERCADOPAGO_WEBHOOK_SECRET`) or `--secret-file` —
never from an argument —, posts it to the route (`--url`, default
`http://127.0.0.1:8000/webhooks/mercado-pago`) and prints the response's
status and body. It exits with `1` when the route does not answer 2xx and
with `2` on a usage error. With `--fake-api`, `--order-id` also takes the
create's `external_reference` (the service's `correlation_id`). Step by
step, with measured output:
[Mercado Pago: testing webhooks »](../mercado-pago-webhooks.md#manual-testing-the-service-running-mercado-pago-simulated).

## Agents — `tempest agents`

```bash
tempest agents tools --agent src.agents:agent     # which tools it holds
tempest agents run "summarise the report" --agent src.agents:agent --trace
```

`tools` calls no model — no token is spent, and it works with a backend that
has no credentials configured. It is the quick answer to "does this agent
configuration actually see the tool I just registered?".

!!! warning "The exit code follows `succeeded`, not 'raised nothing'"
    A run cut short by its budget (steps or seconds) **still returns text**.
    Treating that text as an answer is the mistake this command refuses to
    make for you: `run` exits 1 and prints the stop reason (`max_steps`,
    `max_seconds`) on `stderr`, with the output on `stdout` either way.

There is no scaffolded `agents` layer, so `--agent` is nearly always needed —
and the command says so rather than pretending a convention exists.

## Integration client — `tempest openapi-client`

Generates Pydantic schemas + a typed HTTP client from a third party's OpenAPI
specification:

```bash
tempest openapi-client https://api.vendor.com/openapi.json --name vendor
tempest openapi-client ./vendor/spec.yaml --name vendor --force
tempest openapi-client <spec> -H "Authorization: Bearer $TOKEN" --schemas-only
```

Writes `<src|app>/integrations/<name>/` with `schemas.py` (one class per
component, with the spec's `title`/`description`/`examples` filled in) and
`client.py` (one `async` method per operation, over an injected `HTTPClient`).

```text
  + src/integrations/vendor/__init__.py
  + src/integrations/vendor/client.py
  + src/integrations/vendor/schemas.py
4 schema(s), 12 operation(s).
```

Details, OpenAPI coverage and limitations in the
[Integration client (OpenAPI) »](../openapi-client.md) recipe.

---

## Export the contract — `tempest openapi-export`

`openapi-client` consumes somebody else's spec; this one writes **yours**.

```bash
# Document on stdout
tempest openapi-export

# To a file (JSON by default)
tempest openapi-export --out contracts/openapi.json

# YAML (needs the [openapi] extra)
tempest openapi-export --out contracts/openapi.yaml --yaml
```

Two things come out of that. The first is feeding a generator — `tempest
openapi-client` itself, a TypeScript client generator, a mock server —
**without booting the service**. The second is committing the document, which
turns a contract change into something the reviewer sees:

```bash
tempest openapi-export --out contracts/openapi.json --check
```

```text
error: contracts/openapi.json is out of date:
  + DELETE /api/items/{item_id}
  - GET /api/legacy
  ~ /api/items/{item_id} changed
Run 'tempest openapi-export --out <file>' to refresh it.
```

Exit 1 when it differs, 0 when it is current. Operations added and removed
come first because those are the ones that break consumers; a change inside
an operation reads as `~ <path> changed`. "The spec changed" is not a
reviewable message — which is why the command names what moved.

!!! tip "In CI"
    Run `--check` in the same job as the tests. The day somebody changes a
    status code, a required field or a path, the PR goes red with the exact
    line instead of the client finding out in production.

## PR description with AI — `tempest pr-prompt`

The branch is finally green and the step everyone skips is left: writing the
Pull Request description. Any assistant writes a good one — what it lacks are
the two things that live in the repository: the **template** the team agreed on
and the **diff** the branch actually produced.

`tempest pr-prompt` assembles both into a single prompt and writes it to
`stdout`, so it pipes straight into whichever assistant you run:

```bash
tempest pr-prompt                               # compares against main, prompt on stdout
tempest pr-prompt develop                       # another base
tempest pr-prompt | claude -p                   # pipe it into the assistant
tempest pr-prompt --out pr_prompt.txt           # write a file to paste from
```

The prompt carries three blocks:

1. **The PR template.** The repository's own wins — it is the contract that
   project's reviewers read. These are looked up, in order:
   `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
   `.github/PULL_REQUEST_TEMPLATE/pull_request_template.md`,
   `.gitlab/merge_request_templates/default.md`, `docs/pull_request_template.md`,
   `.pull_request_template.md` and `pull_request_template.md`. With none of
   them, the SDK's bundled template is used.
2. **The rules** that stop the model from returning the template with its
   placeholders still in it: no undecided `Yes/No`, no `_italic instruction_`,
   no section dropped, and no invented migration, env var or dependency that is
   absent from the diff.
3. **The branch context**: commit subjects, the `--name-status` list of changed
   files, and an excerpt of each file's patch.

!!! info "The diff is the one the forge shows"
    Patches are read as `base...head` (three dots) — the merge-base diff, which
    is what GitHub/GitLab display on the pull request. With two dots, every
    commit that landed on `base` after your branch started would be attributed
    to you.

!!! info "What is complete and what is sampled"
    The **commit** list and the **changed-file** list always go in whole — the
    model always knows *what* changed. What is bounded are the **diff
    excerpts**: by default the 10 files with the most changed lines, each cut
    at 1500 characters. So what gets sampled is *how* it changed.

Excerpts are bounded because the whole diff of a large branch does not fit in
the context and is mostly noise (lock file, changelog, generated migration).
Files enter **ordered by changed lines**, not alphabetically — otherwise the
budget is spent on `.github/` and `CHANGELOG.md` before it reaches the file the
PR is actually about:

```bash
tempest pr-prompt --full                        # every file, whole patch
tempest pr-prompt --max-files 20                # 20 files with a patch (default: 10)
tempest pr-prompt --max-files 0                 # file list only, no patch
tempest pr-prompt --max-chars 4000              # more patch per file (default: 1500)
```

`--full` lifts both bounds at once — use it on a branch small enough to send
whole. It **refuses** to run alongside `--max-files` / `--max-chars` (exit `2`):
silently overriding a number you typed would be worse than complaining.

!!! check "Nothing is dropped silently"
    A truncated patch carries the `excerpt cut` mark, and the files left
    without one become an explicit line in the prompt (`N more changed
    file(s)…`). The model reads a partial context as partial instead of taking
    the fragment for the whole change. The cut also respects line boundaries,
    so half a diff line never survives — the model would read it as code that
    does not exist.

The summary (which template was picked, commit/file counts) goes to `stderr`,
so the pipe stays clean:

```text
template: .github/pull_request_template.md
7 commit(s), 12 changed file(s), 10 excerpt(s), 2 file(s) without a patch.
```

Other options:

```bash
tempest pr-prompt --head feat/other             # describe another branch, no checkout
tempest pr-prompt --lang en                     # rules and bundled template in English
tempest pr-prompt -t docs/my_template.md        # explicit template, wins over all
tempest pr-prompt -p ../other-repo              # run against another repository
```

!!! tip "A missing base falls back to `origin/<base>`"
    A fresh clone usually has no local `main`, only `origin/main`. When the base
    does not resolve, the command tries `origin/<base>` before failing. When
    neither exists it exits with code `2` naming the ref it could not find. When
    the comparison holds no commit and no file, it exits `1` — almost always a
    wrong base.
