# Automated Business Provisioning

When an admin clicks **Add Business** on the Businesses page, this app
automatically:

1. Creates a new private GitHub repo for the customer by cloning
   `whatspilot-business-template` (`provisioning/github_client.py`).
2. Waits briefly for the template's files to finish copying into the
   new repo.
3. Creates a new Render web service pointed at that repo, on the free
   plan, with all the env vars the business-portal app needs to boot
   (`provisioning/render_client.py`).
4. Records the outcome - repo URL, Render service id/URL, and either
   `live` or `failed` with an error message - on the business's row in
   the registry (`provisioning/orchestrator.py`).

This takes roughly 15-20 seconds and runs synchronously as part of the
"Add Business" request - there's no background job or queue involved.
If it fails partway through (e.g. the repo gets created but the Render
call then fails), the business is still registered, the repo URL that
was already created is kept on file, and a **Retry Setup** button
appears on the Businesses page to try again from where it left off.

## Required environment variables

Set these on the `whatspilot-admin` Render service (Settings ->
Environment). All are `sync: false` in `render.yaml`, meaning Render
won't overwrite them on blueprint syncs - you fill them in once by
hand.

| Variable | Purpose | Where to get it |
|---|---|---|
| `GITHUB_TOKEN` | Creates repos under `GITHUB_OWNER` | github.com/settings/tokens -> Generate new token (classic) -> `repo` scope |
| `GITHUB_OWNER` | GitHub account/org new repos are created under | Your GitHub username or org name (e.g. `samramkumarqa`) |
| `GITHUB_TEMPLATE_REPO` | The template repo to clone | Defaults to `whatspilot-business-template` - only change if you rename it |
| `RENDER_API_KEY` | Creates the new web service | dashboard.render.com/u/*/settings#api-keys |
| `RENDER_OWNER_ID` | Which Render workspace to create it in | Run `verify_render_provisioning.py` with only `RENDER_API_KEY` set - it lists your workspace IDs |
| `DATABASE_URL` | This admin app's own full-access Postgres connection. Also the fallback `DATABASE_URL` forwarded to new deployments if `BUSINESS_PORTAL_DATABASE_URL` (below) isn't set | Already set on this app for its own use |
| `BUSINESS_PORTAL_DATABASE_URL` | *Optional.* A least-privilege Postgres connection forwarded to new deployments as their `DATABASE_URL` instead of the one above | Run `provisioning/setup_business_portal_role.py` once - see "Restricted Postgres role for business-portal deployments" below |
| `TWILIO_ACCOUNT_SID` | Forwarded to each new deployment | Twilio Console -> Account Dashboard |
| `TWILIO_AUTH_TOKEN` | Forwarded to each new deployment | Twilio Console -> Account Dashboard (click the eye icon) |
| `TWILIO_VERIFY_SERVICE_SID` | Forwarded to each new deployment (OTP login) | Twilio Console -> Verify -> your Verify Service |
| `TWILIO_WHATSAPP_NUMBER` | Forwarded to each new deployment *unless* that business has its own number set (see below) | Your Twilio WhatsApp Sandbox number, used as the default/fallback |
| `GROQ_API_KEY` | Forwarded to each new deployment *unless* that business has its own key set (see below) | console.groq.com, used as the default/fallback |
| `OTP_CHANNEL` | Forwarded to each new deployment | Defaults to `sms` - only change if you switch OTP delivery |

### Giving a business its own WhatsApp number

By default every business shares the one `TWILIO_WHATSAPP_NUMBER` above
(the Sandbox number), same as before this section existed -
`TWILIO_ACCOUNT_SID`/`AUTH_TOKEN`/`VERIFY_SERVICE_SID` stay shared for
every business too, deliberately: one Twilio account can host many
independent WhatsApp senders under the same WABA, so there's no need
for separate account credentials per business, and Verify Service SID
isn't tied to any one business's number either.

The "Twilio WhatsApp Number" field on the Add Business form (backed by
`customer_numbers.twilio_whatsapp_number`, see
`crm/customer_mapping.py`) lets a specific business use its own number
instead. Filling it in is only useful once you've done two things by
hand, neither of which this pipeline automates:

1. Registered that number as a WhatsApp sender with Twilio/Meta (the
   Self Sign-up flow in Twilio Console - Messaging -> Senders ->
   WhatsApp Senders).
2. Pointed that number's inbound webhook at the specific business's own
   Render URL (`https://<their-service>.onrender.com/webhook`) - not
   the shared admin app or another business's URL.

Set the field when *registering* a new business and it flows through
automatically on first provisioning - `_env_vars_for_business()` in
`provisioning/orchestrator.py` picks it up and uses it instead of the
shared Sandbox number for that deployment. Leave it blank for every
other business; they're unaffected.

For a business that's already live and provisioned, don't use this
field or **Retry Setup** to switch it over - `create_web_service()` in
`provisioning/render_client.py` always creates a *new* Render service,
it never updates an existing one, so re-provisioning an already-live
business would spin up a duplicate deployment rather than update its
number. Instead, update the number by hand in two places: the
`twilio_whatsapp_number` column on that business's row (so it's on
record and any *future* re-provisioning picks it up correctly), and the
`TWILIO_WHATSAPP_NUMBER` env var on that business's *existing* Render
service directly (Render dashboard -> that service -> Environment).

### Giving a business its own Groq AI key

By default every business shares the one `GROQ_API_KEY` above - one Groq
account serves every customer's AI replies, lead scoring, and follow-up
drafting. The "Groq API Key" field on the Add Business form (backed by
`customer_numbers.groq_api_key`, see `crm/customer_mapping.py`) lets a
specific business use its own individually-allocated key instead -
useful once a business's usage grows enough that you want its AI
rate limit/cost isolated from every other business rather than all of
them competing for the same shared account.

Unlike the Twilio number above, there's no manual out-of-band step
required first - a Groq key from [console.groq.com](https://console.groq.com)
(API Keys page) works as soon as it's pasted into the form. Set it when
*registering* a new business and it flows through automatically on
first provisioning, same mechanism as `twilio_whatsapp_number`. Leave it
blank for every other business; they keep using the shared key
unaffected.

The key is treated as a real credential, not a display value like the
phone number above: `GET /business-registry` (the list the Businesses
page loads) never returns the raw key, only whether one is set
(`groq_api_key_configured`) - so it isn't re-exposed to the browser on
every page load. It's only echoed back once, directly in the response
to the registration request that just set it.

For a business that's already live and provisioned, same caveat as the
Twilio number: don't use this field or **Retry Setup** to switch it
over, since that would spin up a duplicate Render service. Update it by
hand in two places instead: the `groq_api_key` column on that business's
row, and the `GROQ_API_KEY` env var on that business's *existing* Render
service directly (Render dashboard -> that service -> Environment).

## Before it'll work: Render's GitHub access

Render needs its own GitHub App integration to already have access to
a repo before it can deploy from it - a separate thing from
`GITHUB_TOKEN` above, which only lets *this app* create the repo in
the first place. Check Render Dashboard -> Account Settings -> GitHub,
and make sure it's set to **All repositories** rather than a hand-picked
list. Otherwise every new repo this pipeline creates would need to be
manually added to Render's access list before Render could deploy it -
defeating the point of automating this.

## Verifying the pieces independently

Two standalone scripts exist for testing each API client against your
real accounts without going through the full admin flow - useful when
something's not working and you want to isolate which half is at
fault:

```
GITHUB_TOKEN="..." GITHUB_OWNER="..." python verify_github_provisioning.py
RENDER_API_KEY="..." RENDER_OWNER_ID="..." GITHUB_TEST_REPO_URL="..." python verify_render_provisioning.py
```

Both create a real, throwaway test resource (a repo / a service) that
you should delete afterward.

## Restricted Postgres role for business-portal deployments

Every business-portal deployment is a customer-facing app - more attack
surface than this admin app - so it shouldn't hold the same full-access
Postgres credential this app uses for itself. `provisioning/setup_business_portal_role.py`
creates a dedicated, least-privilege role (`whatspilot_business_portal`)
that can read/write only the tables business-portal code actually uses,
can only *read* the `customer_numbers` tenant registry (never write to
its own or another business's row), and isn't a Postgres superuser or
able to `CREATEDB`/`CREATEROLE`.

Run it once, locally, against production:

```
DATABASE_URL="<production Internal/External Database URL>" python provisioning/setup_business_portal_role.py
```

It prints a new connection string once - copy it into this app's Render
environment as `BUSINESS_PORTAL_DATABASE_URL` immediately, it isn't
saved anywhere. New businesses provisioned after that pick it up
automatically (see `_env_vars_for_business()` in
`provisioning/orchestrator.py`); it's optional, and provisioning falls
back to the admin app's own `DATABASE_URL` until you've run this.
Already-provisioned businesses keep using whatever `DATABASE_URL` they
were given at the time - update their own Render env vars by hand if
you want them covered too.

**Known limitation:** Postgres requires table *ownership* to run
`ALTER TABLE`, and ownership can't be granted piecemeal - so a future
code change that adds a genuinely new *column* to an existing table
needs that one `ALTER TABLE` run once by hand with the original
(owner) `DATABASE_URL`, not by the restricted role. This doesn't affect
day-to-day operation (every deployment's own migration code already
skips columns that already exist) and doesn't apply to brand new
*tables* (the restricted role can create and will own those itself).
See the script's own docstring for the full reasoning.
