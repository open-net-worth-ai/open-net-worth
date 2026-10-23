---
title: Log a spend from your phone
description: Log a purchase the moment you pay — an iPhone shortcut on every Apple Pay tap, an Android automation, or a Telegram message — plus the capture API they all use.
category: features
order: 150
audience: [user, operator]
status: stable
related: [features/settings, connectors/telegram, operations/exposing-beyond-localhost, operations/security-and-privacy]
keywords: [iphone, ios, shortcut, apple pay, wallet, transaction automation, android, tasker, macrodroid, telegram, capture, api, setup code, tap to log]
---

# Log a spend from your phone

Your phone can tell Open Net Worth about a purchase the moment you make it. It sends one
sentence — *"Spent 4.50 at Blue Bottle with Visa"* — and Lucius works out the account, amount,
payee and category and books it. You don't format anything.

| Phone | How | Needs your instance reachable from the phone? |
|---|---|---|
| **iPhone** | The **Log Apple Pay to Open Net Worth** shortcut, run on every Apple Pay tap | Yes |
| **iPhone, iOS 27+** | The **Log Finance Notifications to Open Net Worth** shortcut, run on your bank, wallet, broker and crypto apps' notifications | Yes |
| **Android** | An automation app (Tasker, MacroDroid) forwarding your bank's payment notification | Yes |
| **Any phone** | A message to your Lucius bot on [Telegram](../connectors/telegram.md) | No |

Everything is in **Settings → Phone shortcuts**, also reachable from **Accounts → ⋯ → Phone shortcuts**.

## Your phone has to reach the app

The shortcut and the Android automation talk to your instance directly, usually from a shop,
not your home Wi-Fi. So the address you give them must work from anywhere:

- **Tailscale** (recommended): install it on the phone and on the computer running Open Net Worth,
  and use the computer's Tailscale address (`http://100.x.y.z:8080` or its `.ts.net` name).
  Nothing is exposed to the internet.
- **A public HTTPS address**: a reverse proxy or tunnel. Use `https://`, never plain `http://`:
  your API token travels with every request.
- `localhost`, `127.0.0.1` or a home-network address (`192.168.…`) won't work away from home.
  Settings warns you when the address looks like one of these.

See [Exposing this beyond localhost](../operations/exposing-beyond-localhost.md) for the setup.
**Telegram needs none of this**: the bot connects out to Telegram, so nothing has to reach in.

## iPhone

### 1. Open the setup page

**Settings → Phone shortcuts → iPhone**:

- **On a computer**: pick **iOS 27+** or **iOS 26 or earlier** to match your iPhone, and the QR
  code is already there. Check that the address above it is one your phone
  can reach, then scan it with the iPhone camera. A fresh QR replaces it before it expires. If you
  opened the app at `localhost` (the desktop app does), type your phone's address once (your
  tunnel, Tailscale or HTTPS address). It's remembered from then on, and so is any address you've
  opened the app through.
- **On the iPhone** (Safari or the installed app): the iPhone tab shows the setup code and an
  **Install** button for each shortcut, right there. From the installed home-screen app,
  **Install** opens Safari to download the file; tap **◀ Open Net Worth** at the top left to come
  back.

The setup page works for 10 minutes, and only on the first device that opens it. It shows your
setup code, so don't share the QR or the link.

### 2. Copy your setup code and install the shortcut

On the setup page:

1. Tap **Copy setup code**. The code is your instance's address and API token joined by `|`.
   Treat it like a password.
2. Tap **Install** and **Download**, then open the file from Safari's downloads (the arrow in the
   address bar) and tap **Add Shortcut**. iOS always downloads a shortcut first; no website can
   open it in Shortcuts directly.
3. Open the shortcut in the Shortcuts app, tap the Text at the top that says **Paste your setup
   code here**, and paste the code from step 1. (Some iOS versions ask for it while installing.
   If yours did, it's already filled in.)

The shortcut is signed, so iOS installs it without any "untrusted" setting.

### 3. Run it on every Apple Pay tap

There are two versions of **Log Apple Pay to Open Net Worth**, one per iOS, because iOS 27 moved
the trigger into the shortcut itself. **Settings → Phone shortcuts** has an **iOS 27+ / iOS 26 or
earlier** switch, preset from your iPhone, and the QR and the setup page follow it. Installing
never keeps a trigger, so this step is always yours, once:

**iOS 27 and later.** Open the shortcut, then **Search** (bottom) → **Automation** → **Wallet**
("When I tap a Wallet Card or Pass") → pick your cards. The trigger appears at the top of the
shortcut. No separate automation.

**iOS 26 and earlier.** Create an automation that runs it:

1. Shortcuts → **Automation** → **+** → **Wallet**.
2. **When I tap**: pick your cards. Set it to **Run Immediately**.
3. **Do**: choose **Log Apple Pay to Open Net Worth**.

The shortcut builds the sentence itself from the tap:
`Spent [Amount] at [Name] ([Merchant]) with [Card or Pass] on [time]`, where the time is ISO, e.g.
`2026-10-13T12:58:04+08:00`. It includes both
Name and Merchant because some banks fill only one. Lucius uses whichever is there.

### 4. Test it

Make one small real Apple Pay payment. A notification shows Lucius's own summary of what it
booked. Running the shortcut by hand from the Shortcuts app sends an empty sentence, because
there's no transaction to read.

### Changing the address or token later

The installed shortcut is yours to edit. Open it and change the setup code in the **Text** action
near the top. After you **rotate your API token** (Settings → Security), the shortcut stops
working until you paste the new code. In **Settings → Phone shortcuts**, the **Setup code** field
shows it masked: **Reveal** to see it, **Copy** to copy it.

## Finance notifications (iOS 27)

On iOS 27, a shortcut can start itself when an app sends a notification. **Log Finance Notifications
to Open Net Worth** uses that: whenever one of your bank, wallet, broker or crypto apps notifies you, it sends the
notification's text to Lucius:

> Notification from DBS digibank: Card transaction — — You spent SGD 12.40 at STARBUCKS
> (received 2026-10-13T13:04:22+08:00)

Lucius books purchases, refunds, money received and transfers between your own accounts in the
right direction. It logs nothing for one-time codes, promotions or statements.

1. On the setup page (or **Settings → Phone shortcuts** on the iPhone), install **Log Finance
   Notifications** and paste the same setup code.
2. Open it and add its trigger, since installing doesn't keep one: **Search** (bottom) →
   **Automation** → **Notification** ("When I receive a notification from"). The step appears at
   the top saying *Configure "App" to enable this automation*: tap **App** and pick your bank and
   wallet apps. Optionally, **Add Filter** (e.g. Body contains your currency) so only payment
   alerts run it.
3. **Don't pick a bank whose card already runs the Wallet shortcut.** A purchase would arrive
   twice, once from the tap and once from the bank's alert, and both would be booked. Use the
   Apple Pay shortcut for Apple Pay cards and notifications for everything else.

Every notification from a picked app is one AI call, including the ones that log nothing, so pick
only the apps that send payment alerts.

## Build the iPhone shortcut by hand

If a shortcut won't install, build the direct one yourself. Each step is one action in the
Shortcuts editor:

1. **Text**: your address (e.g. `https://you.example.com`) → **Set Variable** `ONW Base URL`.
2. **Text**: your API token → **Set Variable** `ONW Token`.
3. **Text**: `Spent [Amount] at [Name] ([Merchant]) with [Card or Pass] on [Current Date]`. Insert
   each bracket from **Shortcut Input**. Insert **Current Date** from the variables menu, tap it,
   and set Date Format to **ISO 8601** with **ISO 8601 Time** on. Left as is, it uses your phone's
   default style, which has no time zone.
4. **Get Contents of URL**: `[ONW Base URL]/api/capture`, Method **POST**, headers
   `Authorization: Bearer [ONW Token]` and `Content-Type: application/json`, Request Body **JSON**
   with `text` = the Text from step 3, `source` = `ios-wallet`, and `ref` = `wallet-tap:` followed by
   **Current Date** set to Date Format **ISO 8601** with **ISO 8601 Time** on.
5. **Get Dictionary Value** `reply` → **Show Notification**.

Then make the automation as in [step 3](#iphone) above.

## Android

Android has no Wallet trigger an app can hook into, so you forward the payment notification your
bank's app already shows. **Settings → Phone shortcuts → Android** shows the request to send,
with your API token masked. **Reveal token** shows it, **Copy request** copies the whole request
with the token filled in, and **Copy API token** / **Copy address** copy one piece.

### MacroDroid

1. **Trigger**: Notification Received → your bank's app (optionally only when the text contains
   "spent" or your currency symbol).
2. **Action**: HTTP Request, Method **POST**, URL `https://<your address>/api/capture`, header
   `Authorization: Bearer <your API token>`, and a JSON body:
   `{"text": "<title> <text>", "source": "android-macrodroid"}`. Insert the notification's title
   and text from MacroDroid's magic-text menu.
3. Optional: show the response's `reply` in a notification.

### Tasker

1. **Profile**: Event → UI → Notification → your bank's app.
2. **Task**: Net → HTTP Request, Method **POST**, URL `https://<your address>/api/capture`,
   header `Authorization:Bearer <your API token>`, Content Type `application/json`, body
   `{"text": "<title> <text>", "source": "android-tasker", "ref": "%TIMEMS"}`. Insert the
   notification's title and text from the event's variables (the variable picker lists them).

Either way, the automation only forwards text. It doesn't need to understand it.

`ref` is optional. Without it, every notification books, including two identical purchases. With
it, a retry of the same notification is ignored instead of booked twice. Tasker's `%TIMEMS` (the
current time in milliseconds) is unique per payment, so it's the easy choice. In MacroDroid, use
any magic-text value that differs per notification, or leave `ref` out.

## The capture API

Every method above uses one endpoint, which anything that can send an HTTP request can call.

```
POST /api/capture
Authorization: Bearer <your API token>
Content-Type: application/json

{"text": "Spent $12.40 at Starbucks (Starbucks) with Apple Card on 28 Jul 2026 at 9:15 AM",
 "source": "ios-wallet",
 "ref": "wallet-tap:2026-07-28T09:15:04+08:00"}
```

| Field | Required | What it does |
|---|---|---|
| `text` | Yes | The spend, as a sentence. Up to 2000 characters. |
| `source` | No | A label for where it came from. It shows in the transaction's history as "via …". |
| `ref` | No, but use it | An idempotency key. If a transaction already carries this `source` + `ref`, the request is answered **"Already logged."** and nothing is booked. |
| `wait` | No | Seconds to wait for Lucius (0–50, default 50). Past that the reply is `202` with `pending: true` and the result arrives as a notification. |

**Make `ref` different for every payment**, e.g. the tap or notification time to the second.
A `ref` built from the purchase itself (amount + merchant) makes the second of two identical
coffees "Already logged", and it is never booked. Sending the *same* `ref` again is how a retry
after a timeout avoids booking twice. Two identical purchases sent with the same `source` and
different `ref`s both book: they are never flagged as duplicates of each other.

Different `source`s are different reporters, so their spends *are* checked against each other.
If the purchase is already in the ledger, Lucius gets the existing transaction back instead of
booking a second one.

The iPhone shortcuts each have their own `source`: `ios-wallet` for an Apple Pay tap and
`ios-notification` for an app's notification. A tap's `ref` is `wallet-tap:` and the tap time
to the second with its time zone. A notification's is `notif:`, the notification's own time,
`@` and the app that sent it (e.g. `notif:2026-10-13T13:04:22+08:00@DBS digibank`). So:

- An Apple Pay tap and the bank's notification for the same purchase are booked once.
- Two apps notifying the same purchase (the card's bank and the merchant's app) are booked once.
- Re-running the shortcut for the same alert never books it twice.

Shortcuts from before this change send `source` = `ios-shortcut` for both and don't get this
check. Reinstall them from the setup page.

Captures are handled one at a time, in the order they arrive. A tap and its notification
usually land seconds apart, so the second waits for the first to be booked, then sees it.
Lucius takes 20–50 seconds a capture, so in a burst the phone may get "Still logging this in
the background" and the result as a notification.

The reply carries Lucius's own summary in `reply`, which is what the iPhone shortcut shows.

## Troubleshooting

- **"Importing unsigned shortcut files is not supported"**: you have a file from an older
  version, or one from `docs/shortcuts/dist/` in the repository. Install from the setup page
  instead, or [build it by hand](#build-the-iphone-shortcut-by-hand).
- **The setup page says the shortcut "is not signed in this build"**: this build of the app
  shipped without signed shortcuts. [Build it by hand](#build-the-iphone-shortcut-by-hand).
- **The setup page says the link "expired, or was already opened on another device"**: a setup
  link works for 10 minutes, only on the first device that opens it (a reload there is fine), and
  never after you rotate your API token or change your password. Make a new one in Settings.
- **The shortcut fails with a connection error away from home**: the address only works on your
  home network. See [Your phone has to reach the app](#your-phone-has-to-reach-the-app).
- **"Not logged: no answer from Open Net Worth…"**: something answered, but not the app. It may
  be offline, or the address in your setup code is wrong.
- **`401`**, or **"Not logged: Open Net Worth said "authentication required""**: wrong token.
  Copy the setup code again, after a rotation especially.
- **"…Lucius's AI provider failed 3 times. Try again later"**: the AI provider was unreachable.
  A capture that hasn't written anything is retried up to 3 times before it gives up; nothing
  was booked, so send it again later.
- **`429`**: more than 20 captures a minute. The cap stops a misfiring automation from running up
  your AI bill; wait and retry.
- **"Already logged" for a purchase that isn't**: your `ref` repeats across payments. See the capture API section above.
- **What did Lucius do with a capture?** Every capture keeps its conversation: the text that came
  in, what Lucius booked, and why. Find it in the Lucius **Captures** list, under **Capture · …**.
- **It booked to the wrong account**: name the card the way your account is named, e.g.
  `… with the Meridian Multiplier card`, or rename the account to match the card.
