---
title: REST API
description: Point Lucius at any HTTP API — your bank's, a tool you self-host, a service with no connector of its own — and let it request the data and record what it finds.
category: connectors
order: 45
audience: [user, operator]
status: stable
related: [connectors/external-mcp, connectors/bank-brokerage, lucius/permissions, lucius/automations]
keywords: [rest, api, http, openapi, swagger, generic connector, custom api, api key, bearer token, endpoint, json, self-hosted, webhook, get, post]
---

# REST API

The REST API connector lets Lucius call an HTTP API you choose. Use it when the thing you want
data from has an API but no connector here: a bank or card with a developer API, a budgeting or
invoicing tool, something you host yourself.

You tell the app where the API is, how to sign in, and what can be called. Lucius then requests
the data, reads the answer, and records what it finds — transactions go into your ledger through
the same steps as anything else Lucius adds, so you see and approve them the same way.

Each API you add is a connector of its own, with its own page, its own Pause and Remove, and
its own tool permissions. Add as many as you like.

## Add an API

1. Open **Connectors → Browse & add → REST API**.
2. Fill in the form (below) and press **Save**. The API appears in your connectors list.
3. Ask Lucius to use it: "pull last month's transactions from Acme Bank".

To add another, open **REST API** in Browse & add again.

### Start from an OpenAPI document (optional)

If the API publishes an OpenAPI or Swagger document, paste its address (often
`…/openapi.json`) or the document itself into **OpenAPI document** and press **Read document**.
The app fills in the base URL, the sign-in type and the list of endpoints. Everything it filled
in can be changed, and you still type the key yourself — a document never contains it.

A very large document is read up to its first 500 endpoints. Remove the ones you don't need:
a short list is easier for Lucius to use.

No document? Skip the box and fill the form in by hand.

### The fields

| Field | What to enter |
|---|---|
| **Name** | What you and Lucius call this API. |
| **Base URL** | The start of every address, such as `https://api.example.com/v1`. Lucius only chooses what comes after it. |
| **Authentication** | How the API recognises you: none, a bearer token, an API key in a header, an API key in the URL, or a username and password. |
| **Extra headers** | Optional. Sent with every request — an API version header, for example. |
| **Endpoints** | What can be called: a method, a path such as `/accounts/{account_id}/transactions`, a line on what it returns, and its parameters. |
| **Notes for Lucius** | Optional. Anything that helps: how paging works, the date format, which sign means money out. |

To change an API later, open its connector and choose **Edit configuration**. A saved key shows
as dots; leave it blank to keep it, or type a new one to replace it.

## Every endpoint is a tool

Each endpoint you list becomes a tool Lucius can call. Its parameters are the tool's inputs: a
value in the path (`{account_id}`), a query parameter, or a field of the request body. Lucius
fills those in and nothing else — the method and the path are the endpoint's, fixed.

The connector's page lists the tools under **Tool permissions**, one row per endpoint, so you
decide endpoint by endpoint: a POST that only searches can be **Always allow** while a POST that
moves money stays **Needs approval**.

Three more tools come with every API:

| Tool | What it does |
|---|---|
| `…__list_endpoints` | Shows Lucius the endpoints and your notes. |
| `…__get_data` | A GET to any path under the base URL, for a path you didn't list. |
| `…__send_request` | A POST, PUT, PATCH or DELETE to any path under the base URL. |

If you want the endpoints you listed to be all Lucius can call, set `…__get_data` and
`…__send_request` to **Blocked**.

Editing an endpoint keeps its tool, so an Automation that names it carries on working. If you
change an endpoint's method or path, an **Always allow** on it goes back to **Needs approval**:
the permission was given for the old request. Changing the base URL does the same for every
tool of that API. Changing the key or a description does not.

Removing an endpoint removes its tool; an Automation that named it then reports an unknown tool
rather than doing something else.

## What Lucius can and cannot do

- **Requests only go to the address you saved.** Lucius cannot name another host, and if the
  API redirects to a different host, or outside the base URL, the app does not follow.
- **Your key never reaches Lucius.** The app adds the key and your extra headers to each
  request. Lucius can't read them, set a header, or see the base URL — only the host name.
- **You decide what runs without asking.** Every tool starts at **Needs approval**, so the
  first call shows a confirm card; choose **Always allow** on the ones you want Lucius to use on
  its own. With the new permissions ([Permissions v2](../lucius/permissions-v2.md)) a GET runs
  from the start and only the tools that can change something ask.
- **Private addresses.** On your own install the base URL may be an address on your own
  network. A hosted install only reaches public addresses.

## Large answers

Lucius gets the whole answer or none of it — the app never hands over part of a list, because
half a month of transactions looks like a whole one. An answer over 1 MB is refused with a
message asking for less, and Lucius tries again with a smaller page or date range. If your API
can't page or filter, say so in **Notes for Lucius** and name the smallest request that works.

## Running it on a schedule

Set the tools the run needs to **Always allow** first, so a run with nobody watching isn't left
waiting on a card. Then create an [Automation](../lucius/automations.md) that says what to fetch
and where it goes, for example: "Every morning, get yesterday's transactions from Acme Bank and
add the new ones to my Checking account." Lucius checks each row against what is already in the
ledger, so running it twice doesn't book anything twice.

## Pause or remove

**Pause** on an API's connector stops Lucius calling it and keeps everything saved. **Remove**
deletes that API, its key and its tool permissions. Other APIs are not affected.

## When something goes wrong

| What you see | What to do |
|---|---|
| A 401 or 403 from the API | The key is wrong or expired. Edit the API and type a new one. |
| A 404 | The path is wrong. Check the endpoint against the API's documentation. |
| "This API's address cannot be reached" | The base URL points somewhere the app won't connect to, such as a private address on a hosted install. |
| "The API redirected to a different host or outside its base URL" | Use the address it redirects to as the base URL. |
| "The response is … over the limit" | Ask for a smaller page or date range, or add a note telling Lucius how. |
| "That is not an OpenAPI or Swagger document" | The address returned something else (often a sign-in page). Paste the document instead, or add endpoints by hand. |
| Lucius says a tool is unknown | The endpoint was removed, or the API was. Its connector page shows the tools it has now. |
