import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import {
  cp,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const sourceExecutable = process.argv[2];
assert.ok(
  sourceExecutable,
  "Usage: node qa/packaged-smoke.mjs '/absolute/App.app/Contents/MacOS/Open Net Worth' (macOS) or C:\\path\\open-net-worth.exe (Windows)",
);
const windows = process.platform === "win32";
const profile = await mkdtemp(path.join(tmpdir(), "onw-packaged-profile-"));
// macOS packages an .app bundle; Windows packages a folder holding the .exe and resources/.
const sourceApp = windows
  ? path.dirname(path.resolve(sourceExecutable))
  : path.dirname(path.dirname(path.dirname(path.resolve(sourceExecutable))));
if (!windows) assert.ok(sourceApp.endsWith(".app"), "Expected a macOS app executable");
const relocatedApp = path.join(
  profile,
  "App With Spaces",
  path.basename(sourceApp),
);
await cp(sourceApp, relocatedApp, { recursive: true, verbatimSymlinks: true });
const executable = path.join(
  relocatedApp,
  windows ? "" : "Contents/MacOS",
  path.basename(sourceExecutable),
);
await mkdir("qa/artifacts", { recursive: true });
await mkdir(path.join(profile, "book"));
await writeFile(
  path.join(profile, "desktop-settings.json"),
  JSON.stringify({
    background: false,
    launchAtLogin: false,
    notifications: true,
    selectedBookFolder: await realpath(path.join(profile, "book")),
    books: {},
  }),
);
const forbidden = path.join(profile, "ambient-book-must-not-exist");
const env = {
  ...process.env,
  PATH: windows
    ? `${process.env.SystemRoot}\\System32;${process.env.SystemRoot}`
    : "/usr/bin:/bin:/usr/sbin:/sbin",
  ONW_ROOT: forbidden,
  ONW_DESKTOP_ROOT: forbidden,
  ONW_DESKTOP_REPO: windows ? "C:\\missing-checkout" : "/missing-checkout",
  ONW_DESKTOP_PYTHON: windows ? "C:\\missing-python.exe" : "/missing-python",
  ONW_QA_MODE: "1",
  PYTHONPATH: "/missing-python-path",
};
const child = spawn(path.resolve(executable), [`--user-data-dir=${profile}`], {
  env,
  stdio: ["ignore", "pipe", "pipe"],
});
let diagnostic = "";
let spawnError;
child.on("error", (error) => {
  spawnError = error;
});
child.stdout.on("data", () => {});
child.stderr.on("data", (bytes) => {
  if (diagnostic.length < 20000) diagnostic += bytes.toString();
});
async function poll(fn, timeout = 60000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Packaged application did not become ready");
}
let owner;
let origin;
const exited = () => child.exitCode !== null || child.signalCode !== null;
try {
  await poll(async () => {
    if (spawnError) throw spawnError;
    if (exited())
      throw new Error(
        `Packaged host exited (${child.exitCode}): ${diagnostic}`,
      );
    try {
      owner = JSON.parse(
        await readFile(path.join(profile, "book/.instance.lock"), "utf8"),
      );
      origin = `http://127.0.0.1:${owner.port}`;
      return (await fetch(`${origin}/api/health`)).ok;
    } catch {
      return false;
    }
  });
  const command = (
    windows
      ? execFileSync(
          "powershell",
          [
            "-NoProfile",
            "-Command",
            `(Get-CimInstance Win32_Process -Filter "ProcessId=${owner.pid}").CommandLine`,
          ],
          { encoding: "utf8" },
        )
      : execFileSync("ps", ["-p", String(owner.pid), "-o", "command="], {
          encoding: "utf8",
        })
  ).replaceAll("\\", "/");
  assert.ok(
    command
      .toLowerCase()
      .includes(
        windows
          ? "/resources/runtime/python/"
          : "/contents/resources/runtime/python/",
      ),
    command,
  );
  assert.ok(command.includes("backend.cli.desktop"), command);
  assert.equal((await fetch(`${origin}/api/accounts`)).status, 200);
  assert.equal((await fetch(`${origin}/`)).status, 200);
  assert.equal((await fetch(`${origin}/api/desktop/status`)).status, 403);
  assert.equal(
    (
      await fetch(`${origin}/api/accounts`, {
        headers: { Origin: "https://evil.test" },
      })
    ).status,
    403,
  );
  assert.equal(
    (
      await fetch(`${origin}/api/accounts`, {
        headers: { "X-ONW-Desktop-Session": "wrong" },
      })
    ).status,
    401,
  );
  assert.equal(
    await stat(forbidden).then(
      () => true,
      () => false,
    ),
    false,
  );
  const canonical = await realpath(profile);
  assert.ok((await stat(path.join(profile, "book/data/onw.db"))).size > 0);
  child.kill("SIGTERM");
  const quitStarted = Date.now();
  try {
    await poll(exited, 30000);
  } catch (error) {
    // Record what the app is waiting on instead of only a timeout.
    try { diagnostic += `\nQUIT HANG SAMPLE:\n${execFileSync("sample", [String(child.pid), "3"], { encoding: "utf8", timeout: 30000 })}`; } catch {}
    try { diagnostic += `\nPROCESSES:\n${execFileSync("ps", ["-axo", "pid,ppid,stat,command"], { encoding: "utf8" }).split("\n").filter((line) => /open-net-worth|Open Net Worth|python/i.test(line)).join("\n")}`; } catch {}
    throw error;
  }
  console.log(JSON.stringify({ quitMs: Date.now() - quitStarted }));
  await poll(async () => {
    try {
      await fetch(`${origin}/api/health`);
      return false;
    } catch {
      return true;
    }
  }, 10000);
  await writeFile(
    "qa/artifacts/packaged-report.json",
    JSON.stringify(
      {
        executable: path.resolve(executable),
        profile: canonical,
        checks: [
          "standalone bundled interpreter",
          "relocated app outside checkout with spaces in path",
          "no developer tools on PATH",
          "development overrides ignored",
          "local browser access with guarded origin and desktop-only native APIs",
          "isolated database",
          "quit stops backend",
        ],
        exitCode: child.exitCode,
      },
      null,
      2,
    ),
  );
  console.log("PACKAGED_SMOKE_OK", canonical);
} catch (error) {
  await writeFile(
    "qa/artifacts/packaged-failure.json",
    JSON.stringify(
      {
        executable,
        profile,
        error: String(error),
        diagnostic,
      },
      null,
      2,
    ),
  );
  throw new Error(
    `Packaged smoke failed (${profile}): ${error}\n${diagnostic}`,
    { cause: error },
  );
} finally {
  if (!exited()) {
    child.kill("SIGTERM");
    try {
      await poll(exited, 5000);
    } catch {
      child.kill("SIGKILL");
      await poll(exited, 5000);
    }
  }
  if (owner) {
    try {
      process.kill(owner.pid, "SIGTERM");
    } catch {}
  }
}
