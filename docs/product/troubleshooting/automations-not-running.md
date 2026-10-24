---
title: A scheduled Automation didn't run, or says it did something it didn't
description: Check an Automation's run history, find why a run didn't happen or stopped early, and verify that what it reported really landed in your data.
category: troubleshooting
order: 70
audience: [user]
status: beta
related: [lucius/automations, lucius/permissions, troubleshooting/lucius-says-he-cant-do-that, troubleshooting/faq]
keywords: [automation, scheduled task, run history, failed, needs you, paused, time limit, unreliable]
---

# A scheduled Automation didn't run, or says it did something it didn't

## First, look at the run history

1. Click **Automations** in the sidebar (on a phone: **More → Automations**).
2. Open the Automation.
3. Scroll to **History** (on a narrow screen, switch to the **History** tab). Each run shows its
   status:

| Status | What it means | What to do |
|---|---|---|
| **Completed** | The run finished. | Still [check what it did](#check-what-a-run-really-did). |
| **Running** | It's going now. | Wait. |
| **Needs you** | It stopped to ask for your approval. Nothing waiting has been applied. | Open the run and approve or deny. |
| **Failed** | It stopped with an error. | Open the run to read why; see below. |

No runs at all? The History section says **Hasn't run yet** and when it will first run. Click
**Run now** to try it immediately.

## It never ran

| Check | Fix |
|---|---|
| The Automation card shows **Paused**, or the detail says "Paused — nothing will fire it." | Open its menu and choose **Resume**. |
| The detail says "Finished — no runs left on this schedule." | Its schedule has ended (for example a one-off). Edit the schedule to run again. |
| The app wasn't running at the scheduled time. | Automations run only while the app is running — a closed laptop or stopped server misses them. When the app starts again, an overdue Automation runs **once** to catch up; it doesn't replay every missed run. To keep them on time, leave the app running: the desktop app can keep working in the background, or install a boot service with `onw autostart install`. |
| The computer was asleep at the scheduled time. | A sleeping computer runs nothing. Turn on the **Keep awake** switch at the top of **Automations** — it stops idle sleep while Open Net Worth runs (macOS and Windows; on Linux and in Docker, set the machine's own power settings). A closed laptop lid still sleeps unless the laptop is plugged in with an external display. |
| No AI provider is connected, or its key stopped working. | Fix it in **Settings → AI**. Every run needs a model. |

## It failed

Open the failed run; the error is shown at the top. Common ones:

**"This run exceeded the turn time limit and was stopped before it finished."**
The model took longer than allowed — usually a slow provider taking minutes per step.

1. Give the Automation a faster model: open it, choose **Edit automation** from its menu, and
   change **Model** (it can differ from your default model).
2. Or allow more time: **Settings → AI → Turn time limit → Stop a turn after**, and choose
   30 minutes, 1 hour, 2 hours or **No limit** (the default is 20 minutes). This applies to
   every Lucius reply and Automation run.

Anything the run did up to that point was either finished or not applied — nothing is left
half-done.

**"The server restarted while this Automation was running…"**
The app was stopped or restarted mid-run (an update, a reboot, or a developer server reloading
after a file change). Nothing unconfirmed was applied. Click **Run now** to run it again.

**An error from the AI provider** (rate limit, overloaded, invalid key): wait and use **Run
now**, or check your provider settings in **Settings → AI**.

## Check what a run really did

A run's summary is written by the AI model, and a model can occasionally describe work it didn't
actually do — "I categorized 12 transactions" when nothing changed. This is more common with
smaller, cheaper models. So for anything that touches money:

1. Open the run and look at the actions it actually took, not just the final message. You can
   reply to the run to ask it what it changed.
2. Look at the real data — the account register, the Plan screen — for the changes it claims.
3. If the summary and the data disagree, trust the data. Switch the Automation to a stronger
   model (its **Model** setting) and run it again.

Until this page's status changes from beta, treat any Automation's report as "probably right,
check before you rely on it".

## It changed things without asking me

Each Automation has its own approval setting:

- **Manually approve** — pauses so you can approve each change (shows as **Needs you**).
- **Automatically approve** — runs on its own, pausing only if something looks unsafe.
- **Skip all approvals** — never pauses.

In **Manually approve**, an action still runs without asking if it's on that Automation's own
**Always allowed** list (tapping **Always allow** on a card during a run adds it there). To review
everything first, use **Edit automation** and choose **Manually approve**, then open the
Automation and **Remove** anything set to **Always allow** under its **Tool permissions**. Transactions an Automation
logs still wait in your review queue (**N to review** in Accounts) in every mode. See
[Automations](../lucius/automations.md#approval-mode)
and [Permissions](../lucius/permissions.md).

## Related

- [Automations](../lucius/automations.md)
- [Lucius says he can't do that](lucius-says-he-cant-do-that.md)
- [FAQ](faq.md)
