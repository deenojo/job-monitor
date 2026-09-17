# Internship Job Monitor

This local monitor checks configured public job sources every five minutes, stores matching internship and full-time roles, and alerts through both ntfy mobile push and Gmail email. Each matching job is delivered at most once per channel.

## Automatic monitoring

Windows Task Scheduler runs `Warp Job Monitor` every five minutes while the user is signed in. The task can run on battery but cannot run while the computer is shut down or asleep.

Each cycle:

1. Reads the configured public Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, a16z, and YC job sources.
2. Keeps internship and full-time postings that match a configured role family.
3. Stores a source-specific identity, first-seen timestamp, matching role, selected resume, and direct application URL in a local SQLite database.
4. Sends the new match through each configured channel independently.

If ntfy succeeds but Gmail fails, the email remains pending for retry on a later cycle without sending a duplicate mobile alert. Existing jobs were baseline-marked when the monitor was installed, so the first run does not send a historical alert backlog.

## ntfy mobile alerts

1. Install the ntfy app on your phone.
2. Select **Subscribe to topic**.
3. Enter the topic generated during setup. It is stored in the user environment variable `JOB_MONITOR_NTFY_TOPIC`.
4. Confirm that the **Job monitor end-to-end test** alert is visible.

Mobile alerts contain the job title, company, location, source, and direct application URL. They never include resume contents or credentials.

## Sources and matching

The initial source lists are in `watchlists.json`:

- Greenhouse: Stripe, Figma, Datadog, Scale AI, and Cloudflare
- Lever: ANYbotics, Xsolla, and Waabi
- Ashby: Ramp, Notion, and OpenAI
- SmartRecruiters: Visa
- Workable: Zego
- Workday: configured public employer endpoints, initially empty
- a16z Jobs and YC Work at a Startup public boards
The matching profile and resume routing are in `profile.json`. The configured role families are AI infrastructure, AI platform, AI engineering, AI product management, generative AI, AI/ML engineering, site reliability engineering, product management, AI evaluation engineering, Chief AI Officer/CAIO, AI architecture, growth engineering, technical product management, and technical program management for infrastructure strategy, delivery, or operations. Executive roles are labeled separately in alerts.

LinkedIn is not queried by this script. Create LinkedIn saved searches manually and enable email and app alerts; LinkedIn’s official alerts operate on a daily or weekly cadence.
## Gmail email alerts

The monitor uses Gmail SMTP (`smtp.gmail.com` on port 465 with TLS) to send job-alert emails. It sends a digest containing the job title, company, location, selected resume filename, and direct application link. It cannot read your mailbox or access job sites.

### One-time Gmail setup

1. Use a Gmail account dedicated to sending these alerts where possible.
2. Enable 2-Step Verification on that account.
3. Go to Google Account **Security** > **App passwords**, create one named `Warp Job Monitor`, and copy the 16-character code. App Passwords are unavailable for accounts using Advanced Protection and may be restricted by Google Workspace administrators.
4. Store the sender address, recipient address, and App Password as local user environment variables. Use the App Password, never the normal Google account password.
   Replace every `{{...}}` placeholder below with its real value before running the command.

```powershell
[Environment]::SetEnvironmentVariable('JOB_MONITOR_GMAIL_ADDRESS', '{{GMAIL_SENDER_ADDRESS}}', 'User')
[Environment]::SetEnvironmentVariable('JOB_MONITOR_ALERT_EMAIL', '{{ALERT_RECIPIENT_ADDRESS}}', 'User')
[Environment]::SetEnvironmentVariable('JOB_MONITOR_GMAIL_APP_PASSWORD', '{{GMAIL_APP_PASSWORD}}', 'User')
```

Close and reopen Warp or PowerShell after setting the variables. Then check configuration and send a labeled test email:

```powershell
& "$HOME\job-monitor\.venv\Scripts\python.exe" "$HOME\job-monitor\job_monitor.py" --email-status
& "$HOME\job-monitor\.venv\Scripts\python.exe" "$HOME\job-monitor\job_monitor.py" --email-test
```

If `JOB_MONITOR_ALERT_EMAIL` is omitted, alerts are sent back to the Gmail sender account. If the App Password is revoked or the Google account password changes, create a new App Password and update only the local user environment variable. Do not commit or share the App Password.

## Verify alert configuration

Check Gmail configuration without displaying any credentials:

```powershell
& "$HOME\job-monitor\.venv\Scripts\python.exe" "$HOME\job-monitor\job_monitor.py" --email-status
```

Expected result:

```text
{
  "configured": true,
  "recipient_configured": true
}
```

Send a Gmail-only test message:

```powershell
& "$HOME\job-monitor\.venv\Scripts\python.exe" "$HOME\job-monitor\job_monitor.py" --email-test
```

The mobile channel can be tested by publishing a labeled message to the configured ntfy topic. Production job notifications are sent only when the monitor discovers a new matching listing.

## Run and check the monitor

The task named `Warp Job Monitor` is already enabled and runs every five minutes while you are signed in. It can run on battery, but does not run while the computer is shut down or asleep.

Run a manual check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\job-monitor\run-monitor.ps1"
```
The command returns a JSON summary. `new_matches` counts first-time matching listings found in that run; `ntfy` and `email` report individual channel outcomes; `source_failures` lists any job source that could not be reached.

Check the scheduled task:

```powershell
schtasks /Query /TN "Warp Job Monitor" /FO LIST /V
```

Within Warp, use the `local-job-monitor` MCP connector:

- `recent_matches` lists stored roles and the appropriate resume path.
- `job_details` retrieves one stored role by its fingerprint.
- `resume_for_role` returns the resume path for a configured role.

## Update the employer watchlists

Edit `watchlists.json` to add public source identifiers:

- Greenhouse identifier: the segment after `boards.greenhouse.io/`
- Lever identifier: the segment after `jobs.lever.co/`
- Ashby identifier: the segment after `jobs.ashbyhq.com/`
- SmartRecruiters identifier: the company ID in its public careers URL
- Workable identifier: the account segment after `apply.workable.com/`
- Workday: add an object with `name` and the employer's complete public `wday/cxs/.../jobs` endpoint

For example, a Greenhouse career URL ending in `/example-company` becomes `"example-company"` in `greenhouse_boards`. Keep JSON valid and use only public company career boards. Indeed is not directly monitored because its documented APIs serve employer partners; add a licensed aggregation API separately if broader Indeed coverage is needed.

Run a manual check after changing the list. Previously unseen matching roles from the new board can trigger one alert.

## Stop or remove monitoring

Disable recurring checks without deleting configuration:

```powershell
schtasks /Change /TN "Warp Job Monitor" /Disable
```

Re-enable it:

```powershell
schtasks /Change /TN "Warp Job Monitor" /Enable
```

Delete the scheduled task permanently:

```powershell
schtasks /Delete /TN "Warp Job Monitor" /F
```

## Boundaries

The monitor reads public job listings, matches roles, and opens no job-site sessions. It does not submit applications, store job-board passwords, answer eligibility questions, or make declarations on the user’s behalf. Review every application before submission.
