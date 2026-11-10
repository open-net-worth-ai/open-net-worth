// The official client: the referee for what the app wrote, and the person using real Actual.
// One job per run, read as JSON from stdin; its answer goes to the file the job names.
// Copied beside the pinned node_modules by the suite. Made-up data only.
import * as api from '@actual-app/api';
import fs from 'node:fs';

const job = JSON.parse(fs.readFileSync(0, 'utf8'));
const FROM = '2000-01-01';
const TO = '2040-01-01';

if (job.clockSkewMs) {
  const now = Date.now.bind(Date);
  Date.now = () => now() + job.clockSkewMs;
}

fs.mkdirSync(job.dataDir, { recursive: true });
const lib = await api.init({
  dataDir: job.dataDir,
  serverURL: job.server,
  password: job.password,
});

async function allRows() {
  const rows = [];
  for (const account of await api.getAccounts()) {
    for (const row of await api.getTransactions(account.id, FROM, TO)) {
      rows.push({ ...row, account_name: account.name });
    }
  }
  return rows;
}

async function rowByNotes(notes) {
  const found = (await allRows()).filter((row) => row.notes === notes);
  if (found.length !== 1) throw new Error(`${found.length} rows noted ${notes}`);
  return found[0];
}

async function view({ months = [] } = {}) {
  const accounts = [];
  for (const account of await api.getAccounts()) {
    accounts.push({
      id: account.id,
      name: account.name,
      offbudget: !!account.offbudget,
      closed: !!account.closed,
      balance: await api.getAccountBalance(account.id, new Date(TO)),
    });
  }
  const rows = (await allRows()).map((row) => ({
    id: row.id,
    account: row.account_name,
    date: row.date,
    amount: row.amount,
    notes: row.notes ?? null,
    category: row.category ?? null,
    payee: row.payee ?? null,
    imported_id: row.imported_id ?? null,
    transfer_id: row.transfer_id ?? null,
    cleared: !!row.cleared,
    subs: (row.subtransactions ?? []).map((line) => ({
      id: line.id,
      amount: line.amount,
      category: line.category ?? null,
      notes: line.notes ?? null,
    })),
  }));
  const categories = (await api.getCategories()).map((c) => ({
    id: c.id,
    name: c.name,
    hidden: !!c.hidden,
    is_income: !!c.is_income,
    group: c.group_id,
  }));
  const groups = (await api.getCategoryGroups()).map((g) => ({
    id: g.id,
    name: g.name,
    hidden: !!g.hidden,
    is_income: !!g.is_income,
  }));
  const budget = {};
  for (const month of months) {
    const sheet = await api.getBudgetMonth(month);
    budget[month] = {
      toBudget: sheet.toBudget,
      forNextMonth: sheet.forNextMonth,
      categories: Object.fromEntries(
        sheet.categoryGroups
          .flatMap((group) => group.categories)
          .map((c) => [c.id, { budgeted: c.budgeted, spent: c.spent, balance: c.balance }]),
      ),
    };
  }
  const payees = (await api.getPayees()).map((p) => ({ id: p.id, name: p.name, transfer: p.transfer_acct ?? null }));
  return { accounts, rows, categories, groups, budget, payees };
}

// A budget made the way the app makes one, holding nothing real.
async function seed({ name, month, day, lastDay, tracking = false }) {
  const made = await lib.send('create-budget', { budgetName: name });
  if (made?.error) throw new Error(JSON.stringify(made));
  if (tracking) {
    await lib.send('preferences/save', { id: 'budgetType', value: 'tracking' });
  }
  const checking = await api.createAccount({ name: 'ACME Checking', offbudget: false }, 500000);
  const card = await api.createAccount({ name: 'ACME Card', offbudget: false }, 0);
  const savings = await api.createAccount({ name: 'ACME Savings', offbudget: true }, 100000);
  const group = await api.createCategoryGroup({ name: 'ACME Everyday' });
  const food = await api.createCategory({ name: 'ACME Food', group_id: group });
  const fun = await api.createCategory({ name: 'ACME Fun', group_id: group });
  const income = (await api.getCategories()).find((c) => c.is_income && c.name !== 'Starting Balances');
  const toSavings = (await api.getPayees()).find((p) => p.transfer_acct === savings);
  const toCard = (await api.getPayees()).find((p) => p.transfer_acct === card);
  await api.addTransactions(
    checking,
    [
      { date: lastDay, amount: 300000, payee_name: 'ACME Payroll', category: income.id, notes: 'salary' },
      { date: day, amount: -2599, payee_name: 'ACME Market', category: food, notes: 'plain' },
      { date: day, amount: -1200, payee_name: 'ACME Deli', category: food, notes: 'other row' },
      {
        date: day,
        amount: -10000,
        payee_name: 'ACME Shop',
        notes: 'split parent',
        subtransactions: [
          { amount: -7000, category: food, notes: 'a' },
          { amount: -3000, category: fun, notes: 'b' },
        ],
      },
      { date: day, amount: -25000, payee: toSavings.id, notes: 'to savings', category: fun },
      { date: day, amount: -5000, payee: toCard.id, notes: 'card payment' },
    ],
    { runTransfers: true },
  );
  await api.addTransactions(card, [
    { date: day, amount: -4500, payee_name: 'ACME Cafe', category: fun, cleared: true, notes: 'card row' },
  ]);
  await api.setBudgetAmount(month, food, 50000);
  await api.setBudgetAmount(month, fun, 12500);
  await api.sync();
  const me = (await api.getBudgets()).find((f) => f.name === name);
  return {
    file: me.cloudFileId,
    group: me.groupId,
    ids: { checking, card, savings, food, fun, income: income.id, everyday: group },
  };
}

const operations = {
  call: (name, ...args) => api[name](...args),
  send: (name, payload) => lib.send(name, payload),
  view,
  seed,
  edit: async (notes, fields) => {
    const row = await rowByNotes(notes);
    await api.updateTransaction(row.id, fields);
    return row.id;
  },
  remove: async (notes) => {
    const row = await rowByNotes(notes);
    await api.deleteTransaction(row.id);
    return row.id;
  },
  budgets: () => api.getBudgets(),
  // A window left open: holds this client's downloaded copy, unsynced, until told to go on.
  pause: async (ready, go) => {
    fs.writeFileSync(ready, 'ready');
    while (!fs.existsSync(go)) await new Promise((wake) => setTimeout(wake, 20));
  },
  bulk: async (accountName, count) => {
    const account = (await api.getAccounts()).find((a) => a.name === accountName);
    const rows = Array.from({ length: count }, (_, i) => ({
      date: `20${20 + (i % 6)}-0${1 + (i % 9)}-15`,
      amount: -(100 + i),
      payee_name: `ACME Payee ${i % 200}`,
      notes: `bulk ${i}`,
    }));
    await api.addTransactions(account.id, rows);
    return count;
  },
  // Rules, schedules, tags, account groups, carryover, hold and notes: what this app leaves alone.
  unmodelled: async (month) => {
    const accounts = await api.getAccounts();
    const checking = accounts.find((a) => a.name === 'ACME Checking');
    const food = (await api.getCategories()).find((c) => c.name === 'ACME Food');
    const done = {};
    const attempt = async (what, call) => {
      try {
        await call();
        done[what] = 'ok';
      } catch (error) {
        done[what] = `failed: ${error.message}`;
      }
    };
    await attempt('account group', async () => {
      const id = await api.createAccountGroup({ name: 'ACME Account Group' });
      await api.updateAccount(checking.id, { account_group_id: id });
    });
    await attempt('tag', () => api.createTag({ tag: 'acmetag', color: '#336699', description: 'made up' }));
    await attempt('rule', () =>
      api.createRule({
        stage: 'pre',
        conditionsOp: 'and',
        conditions: [{ op: 'is', field: 'imported_payee', value: 'ACME Rule' }],
        actions: [{ op: 'set', field: 'category', value: food.id }],
      }),
    );
    await attempt('schedule', () =>
      api.createSchedule({
        name: 'ACME Schedule',
        posts_transaction: false,
        account: checking.id,
        amount: -1000,
        amountOp: 'is',
        date: `${month}-28`,
      }),
    );
    await attempt('carryover', () => api.setBudgetCarryover(month, food.id, true));
    await attempt('hold', () => api.holdBudgetForNextMonth(month, 100));
    await attempt('note', () => api.updateNote(food.id, 'a made up note'));
    await attempt('tagged row', () =>
      api.addTransactions(checking.id, [{ date: `${month}-02`, amount: -222, payee_name: 'ACME Tagged', notes: 'lunch #acmetag' }]),
    );
    return done;
  },
};

const results = [];
try {
  if (job.group) {
    await api.downloadBudget(job.group, job.encryption ? { password: job.encryption } : undefined);
  }
  for (const [name, ...args] of job.steps) {
    results.push((await operations[name](...args)) ?? null);
  }
  if (job.steps[0][0] !== 'budgets') await api.sync();
  await api.shutdown();
  fs.writeFileSync(job.answerFile, JSON.stringify({ ok: true, results }));
} catch (error) {
  fs.writeFileSync(job.answerFile, JSON.stringify({ ok: false, error: String(error?.message ?? error), results }));
  process.exitCode = 1;
}
process.exit();
