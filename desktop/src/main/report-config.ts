// Where a problem report goes: the one place the destination is written down. Nothing is sent
// unless the user presses Send on a previewed report. ONW_REPORTS_URL points a report at another
// portal (a test stack, a self-hosted one); backend/core/problem_report.py keeps the same
// values for the browser path, and a test holds them equal.
export const REPORTS_ENDPOINT = 'https://cloud.opennetworth.ai/api/problem-reports';
export const SUPPORT_EMAIL = 'support@opennetworth.ai';
export const COMMUNITY_URL = 'https://discord.gg/eCGBd4zg3B';
export const REPORTS_URL_ENV = 'ONW_REPORTS_URL';

function loopbackHost(host: string): boolean {
  const name = host.toLowerCase().replace(/^\[|\]$/g, '');
  return name === 'localhost' || name === '::1' || /^127\./.test(name);
}

// Plain http is accepted for a loopback test stack only, never credentials in the URL.
export function reportsEndpoint(env: NodeJS.ProcessEnv = process.env): string {
  const value = env[REPORTS_URL_ENV]?.trim() || REPORTS_ENDPOINT;
  let url: URL;
  try { url = new URL(value); } catch { throw new Error('The report address is not a valid URL.'); }
  if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopbackHost(url.hostname)))) throw new Error('The report address must be an https URL without credentials.');
  return url.toString();
}
