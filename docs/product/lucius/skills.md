---
title: Skills
description: Saved playbooks Lucius follows for a job — the built-in ones, how to run them, and how to create, upload or edit your own.
category: lucius
order: 80
audience: [user]
status: stable
related: [lucius/talking-to-lucius, lucius/automations, lucius/permissions, lucius/mentions]
keywords: [skill, skills, SKILL.md, playbook, slash command, starter skills, upload, run]
---

# Skills

A skill is a saved playbook: step-by-step instructions Lucius follows for a particular job —
catching up your books, writing the monthly report, checking whether you can afford something.
Teach him a workflow once ("here's exactly how I want my month-end done") instead of explaining
it every time.

## Use a skill

You don't have to do anything special — when your request matches a skill, Lucius loads it by
himself. To run one deliberately:

- **In chat:** type `/` and pick from the list, or type the name — `/monthly-report` — followed by
  anything it should know: `/monthly-report August`. You can stack up to six in one message.
- **From the Skills tab:** select a skill and click **Run**. It opens in chat and Lucius runs it
  there.

## Built-in skills

A new install comes with these. Find them in **Skills** in the sidebar.

| Skill | Ask for it like |
|---|---|
| `catch-up` | "Catch me up" — new transactions in, duplicates merged, unsorted ones labelled, balances checked, overspending covered before the month rolls |
| `log-transactions-from-email` | "Log my transactions from email" — bank, card and trade confirmations booked, payees cleaned up, nothing twice |
| `daily-briefing` | "How did yesterday go?" — a few friendly sentences on money in and out, the portfolio, bills due and anything to review |
| `weekly-report` | "How did last week go?" — a designed report page |
| `monthly-report` | "How did September go?" — a designed report page |
| `budget-review` | "Am I over budget?" / "Adjust my targets" |
| `balance-mismatch-tracer` | "The app says 1,240 but my bank says 1,180" — matches your bank's own rows (a screenshot, statement or pasted list) against the app's, and names the rows that make up the gap |
| `subscription-audit` | "What am I subscribed to?" — monthly and yearly cost, what went up, what looks forgotten |
| `can-i-afford-it` | "Can I afford a $30k car?" / "How long would my cash last?" |
| `on-track` | "Am I on track?" — savings goals and financial independence |
| `investment-checkup` | "How's my portfolio?" — returns vs benchmark, drift, what your funds hold, fees |
| `onboarding-new-user` | Runs by itself the first time, while the book is still empty |
| `skill-creator` | "Make a skill for…" — design and test your own |

`statement-import` is always there too: it guides every statement import.

The **Daily briefing**, **Weekly report**, **Monthly report** and **Auto-log transactions from
email** [Automations](automations.md) use these skills, so improving a skill improves the
Automation.

Each built-in skill has a switch at the top of its page to turn it off, and **Export** in its
**⋮** menu. The Skills tab doesn't offer **Edit** for built-ins — ask Lucius to change one
instead (*"change the weekly report so it also lists my largest purchase"*). When an app update
improves a built-in skill, a copy you never changed is updated for you; one you changed is left
alone, and so is one an Automation's instructions still name.

## Create your own

1. Open **Skills** and click **+ New skill**.
2. Pick one:

   | Option | What happens |
   |---|---|
   | **Create with Lucius** | A chat where Lucius asks what the skill should do, drafts it and tests it with you. The easiest way. |
   | **Write skill instructions** | A form: **Name** (lowercase letters, numbers and hyphens, e.g. `monthly-close`), **Description** (one line — what it does), and **Instructions** in Markdown. Click **Create skill**. |
   | **Upload a skill** | Drop in a `.zip` containing a `SKILL.md` — for example one exported from here or from Claude Code. **Download a template** gives you a starting point. An uploaded skill is installed **switched off**: review it, then turn it on. |
   | **Browse starter skills** | Add a built-in starter to your library — useful if you removed one. |

3. Check it works: type `/` and your skill's name in chat, or select it and click **Run**.

**Write a good description.** It's what Lucius reads to decide when the skill applies — say what
the skill does *and* when to use it ("Use when I ask for my month-end close or…"). He only reads
the full instructions once he picks the skill.

When Lucius writes or changes a skill for you, it follows the conversation's [approval
mode](permissions.md) like any other change — with **Manually approve** you'll see a card listing
every file he'll write first.

## What a skill is made of

A skill is a folder with one required file, `SKILL.md`: a short header with its `name` and
`description`, then plain Markdown instructions. It can also hold `references/` (details Lucius
reads only when a step needs them), `assets/` (fixed templates, like a report layout) and
`scripts/` (helper scripts — see below).

```
my-skill/
├── SKILL.md
├── references/
├── assets/
└── scripts/
```

It's the same open format Claude Code uses, so most skills move between the two as they are. The
skill editor's **Claude Code** and **OpenAI** tabs hold the extra settings those tools use, such
as **Invocation** (**Both**, **Manual only** — only when you call it with `/`, or **Model only**
— only when Lucius picks it).

## What a skill can't do

**A skill can't grant permissions.** Its instructions can tell Lucius what to do and in what
order, but every action still follows the same [permission level](permissions.md) and approval
mode as outside the skill. "Always send the email without asking" in a skill changes nothing.

**A skill can't be unloaded mid-conversation.** Once Lucius has read it, it stays in that
conversation. For a clean slate, start a new conversation.

## Skills with scripts

A skill that runs its own Python script needs **Settings → Developer → Bundled script skills**
switched on (it is off by default — nothing runs a script without it). On top of that, **Run
script** is an ordinary tool: it starts at **Ask**, and its setting and the approval mode decide
whether it asks first, the same in a chat and in an Automation run.

## Troubleshooting

**The skill doesn't show up when I type `/`.** It's switched off, or set to **Model only**. Turn
it on from its page, or change **Invocation** in the editor.

**Run is greyed out.** *"Enable the skill to run it"* — turn on the switch at the top of its
page. Or *"This skill can only be selected automatically"* — it's set to **Model only**.

**Lucius doesn't use my skill on his own.** Make the **Description** say clearly when it applies,
using the words you'd actually type.

## Related pages

- [Automations](automations.md) — run a skill on a schedule
- [Mentioning things in chat](mentions.md) — `@` and `/` in the message box
- [Permissions](permissions.md) — what a skill can't override
