import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { poll, api } from "./server-client-fixture.mjs";

export async function login(target) {
  await target
    .getByLabel("Password", { exact: true })
    .fill("synthetic-client-password");
  await target.getByRole("button", { name: "Sign in", exact: true }).click();
  try {
    await target
      .getByRole("button", { name: "Settings", exact: true })
      // A cold browser's first load of the app on a busy hosted runner can pass 30s.
      .waitFor({ timeout: 90_000 });
  } catch (error) {
    if (
      await target
        .getByRole("button", { name: "Show Error", exact: true })
        .count()
    ) {
      await target
        .getByRole("button", { name: "Show Error", exact: true })
        .click();
      throw new Error(await target.locator("body").innerText());
    }
    throw error;
  }
}

export async function lockedLocal({
  f,
  app,
  active,
  choice,
  menu,
  connect,
  login,
  check,
}) {
  const lockServer = {
    port: 8096,
    book: f.local,
    origin: "http://127.0.0.1:8096",
    child: null,
    requests: path.join(f.root, "local-lock-requests.jsonl"),
  };
  f.servers.push(lockServer);
  await f.start(lockServer);
  await app.evaluate(({ dialog }) => {
    dialog.qaMessages = [];
    dialog.showMessageBox = async (_window, options) => {
      dialog.qaMessages.push(options.message);
      return {
        response: options.buttons.includes("Keep current connection") ? 1 : 0,
      };
    };
  });
  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: "On this computer", exact: true })
    .click();
  await poll(
    async () =>
      (await app.evaluate(({ dialog }) => dialog.qaMessages)).some((value) =>
        /already open/i.test(value),
      ),
    45000,
  );
  let current = await active();
  await login(current);
  assert.equal((await api(current, "/api/auth/status")).authenticated, true);
  assert.equal(
    JSON.parse(await readFile(path.join(f.profile, "desktop-settings.json")))
      .selectedServerUrl,
    f.servers[0].origin,
  );
  check(
    "locked remembered local book shows its concrete error and keeps the server connection",
  );

  await app.evaluate(({ dialog }, alternate) => {
    dialog.showOpenDialog = async () => ({
      canceled: false,
      filePaths: [alternate],
    });
    dialog.showMessageBox = async (_window, options) => ({
      response: Math.max(0, options.buttons.indexOf("Connect")),
    });
  }, f.alternate);
  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: "On this computer", exact: true })
    .click();
  await poll(async () => {
    current = await active();
    return (
      current &&
      (await current.evaluate(() => typeof window.onwDesktop)) !==
        "undefined" &&
      current.url().startsWith("http:")
    );
  }, 60000);
  assert.equal(
    (await current.evaluate(() => window.onwDesktop.getSettings())).bookFolder,
    f.alternate,
  );
  assert.equal(
    (await api(current, "/api/accounts")).some(
      (row) => row.name === "Client QA Alternate Local",
    ),
    true,
  );
  await menu("Connection…");
  await (
    await choice()
  )
    .getByRole("button", { name: /On your own server/ })
    .click();
  await connect(f.servers[0].origin);
  current = await active();
  await login(current);
  assert.equal(
    JSON.parse(await readFile(path.join(f.profile, "desktop-settings.json")))
      .selectedBookFolder,
    f.alternate,
  );
  check(
    "explicit alternate local folder escapes a locked remembered book without stopping its owner",
  );
  await f.stop(lockServer);
  return current;
}
