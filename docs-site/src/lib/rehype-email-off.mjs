// Cloudflare's Email Address Obfuscation rewrites every address it sees in served HTML into a
// link to /cdn-cgi/l/email-protection, which 404s when Google crawls it — Search Console then
// lists it under "Not found (404)". Cloudflare skips anything between <!--email_off--> and
// <!--/email_off-->, so each element whose text holds an address is fenced with them here,
// at build time, leaving the corpus markdown (also read raw in-app and by Lucius) untouched.
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;

const holdsEmail = (node) =>
  node.type === 'text'
    ? EMAIL.test(node.value)
    : (node.properties?.href && String(node.properties.href).startsWith('mailto:')) ||
      (node.children || []).some(holdsEmail);

function fence(parent) {
  if (!parent.children) return;
  const out = [];
  for (const child of parent.children) {
    const leaf = child.type === 'text' || child.tagName === 'code' || child.tagName === 'a';
    if (leaf && holdsEmail(child)) {
      out.push({ type: 'comment', value: 'email_off' }, child, { type: 'comment', value: '/email_off' });
    } else {
      fence(child);
      out.push(child);
    }
  }
  parent.children = out;
}

export function rehypeEmailOff() {
  return (tree) => fence(tree);
}
