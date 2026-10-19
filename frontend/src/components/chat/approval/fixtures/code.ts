import type { CardFixture } from './types'

const SAVINGS_SCRIPT = `import pandas as pd

rows = pd.DataFrame(input["transactions"])
income = rows.loc[rows["amount"] > 0, "amount"].sum()
spend = -rows.loc[rows["amount"] < 0, "amount"].sum()
rate = (income - spend) / income if income else 0.0
print({"month": input["month"], "savings_rate": round(rate, 4)})
`

export const CODE_FIXTURES: CardFixture[] = [
  {
    name: 'external_mcp_call · ordinary args',
    change: {
      type: 'external_mcp_call',
      id: 'toolu_ext_search',
      server_id: 'notes',
      tool: 'search_notes',
      args: { query: 'rent receipts', limit: 5, filters: { tag: 'housing', archived: false } },
      display: {
        name: 'search_notes',
        server: 'Notes',
        args: { query: 'rent receipts', limit: 5, filters: { tag: 'housing', archived: false } },
      },
      reason: "Run the 'search_notes' tool on the external 'Notes' MCP server.",
    },
  },
  {
    name: 'external_mcp_call · money movement',
    change: {
      type: 'external_mcp_call',
      id: 'toolu_ext_transfer',
      server_id: 'bank',
      tool: 'transfer_funds',
      args: { from_account: 'Everyday Checking', to_account: 'Rainy Day Savings', amount: 250 },
      display: {
        name: 'transfer_funds',
        server: 'Meridian Bank',
        args: { from_account: 'Everyday Checking', to_account: 'Rainy Day Savings', amount: 250 },
        is_money_movement: true,
      },
      reason: "Run the 'transfer_funds' tool on the external 'Meridian Bank' MCP server.",
    },
  },
  {
    name: 'run_code · savings-rate script',
    change: {
      type: 'tool_permission',
      id: 'toolu_code_run',
      grant_tool: 'run_code',
      session_id: 'sess_demo',
      display: {
        tool: 'run_code',
        arguments: {
          purpose: 'Compute the monthly savings rate',
          source: SAVINGS_SCRIPT,
          language: 'python',
        },
      },
    },
  },
  {
    name: 'code_skill_create · savings-rate skill',
    change: {
      type: 'code_skill_create',
      id: 'toolu_code_skill',
      grant_tool: 'save_code_as_skill',
      skill: {
        name: 'Monthly savings rate',
        description: 'Share of income left after spending, for one month',
        body: 'Run the `savings_rate` script with run_script.',
      },
      script: {
        id: 'savings_rate',
        source: SAVINGS_SCRIPT,
        language: 'python',
        filename: 'savings_rate.py',
      },
      display: {
        name: 'Monthly savings rate',
        purpose: 'Share of income left after spending, for one month',
        source: SAVINGS_SCRIPT,
        script_id: 'savings_rate',
        language: 'python',
      },
    },
  },
]
