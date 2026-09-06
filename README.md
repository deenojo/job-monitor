# Internship Job Monitor

This local monitor checks configured public job sources every 10 minutes, stores matching internship roles, and sends an ntfy mobile alert only when it sees a matching listing for the first time.

## Subscribe to alerts

1. Install the ntfy app on your phone.
2. Select **Subscribe to topic**.
3. Enter the topic generated during setup. It is stored in the user environment variable `JOB_MONITOR_NTFY_TOPIC`.
4. Confirm that the **Job monitor end-to-end test** alert is visible.

Alerts contain only the job title, company, location, source, and direct application URL. They never include resume contents or credentials.

## Sources and matching

The initial source lists are in `watchlists.json`:

- Greenhouse: Stripe, Figma, Datadog, Scale AI, and Cloudflare
- Lever: ANYbotics, Xsolla, and Waabi
- a16z Jobs and YC Work at a Startup public boards

The matching profile and resume routing are in `profile.json`. Only postings whose title identifies an internship, co-op, or internship-like role are saved. The configured role families are AI infrastructure, AI platform, AI engineering, AI product management, generative AI, AI/ML engineering, site reliability engineering, and product management.

LinkedIn is not queried by this script. Create LinkedIn saved searches manually and enable email and app alerts; LinkedIn’s official alerts operate on a daily or weekly cadence.

## Run and check the monitor

The task named `Warp Job Monitor` is already enabled and runs every 10 minutes while you are signed in. It can run on battery, but does not run while the computer is shut down or asleep.

Run a manual check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\job-monitor\run-monitor.ps1"
```

Check the scheduled task:

```powershell
schtasks /Query /TN "Warp Job Monitor" /FO LIST /V
```

Within Warp, use the `local-job-monitor` MCP connector:

- `recent_matches` lists stored roles and the appropriate resume path.
- `job_details` retrieves one stored role by its fingerprint.
- `resume_for_role` returns the resume path for a configured role.

## Update the employer watchlists

Edit `watchlists.json` to add a public board identifier:

- Greenhouse identifier: the segment after `boards.greenhouse.io/`
- Lever identifier: the segment after `jobs.lever.co/`

For example, a Greenhouse career URL ending in `/example-company` becomes `"example-company"` in `greenhouse_boards`. Keep JSON valid and use only public company career boards.

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
