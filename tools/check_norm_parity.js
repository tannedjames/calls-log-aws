// Proves cloud/naming.py folds names exactly like the Web App (AD-8): runs the
// real norm_() from apps_script/WebAppSync.gs on tools/norm_cases.json, whose
// expected values tests.py checks against cloud/naming.norm. CI runs this.
//
//   node tools/check_norm_parity.js
//
// Exit 0 = every case matches, 1 = a mismatch, 2 = norm_ not found.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'apps_script', 'WebAppSync.gs'), 'utf8');
const m = src.match(/function norm_\(s\)\{[\s\S]*?\n\}/);
if (!m) {
  console.error('norm_ not found in apps_script/WebAppSync.gs');
  process.exit(2);
}
const norm_ = new Function(m[0] + '\nreturn norm_;')();
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'norm_cases.json'), 'utf8'));

let bad = 0;
for (const [input, expected] of cases) {
  const got = norm_(input);
  if (got !== expected) {
    bad++;
    console.error(`MISMATCH ${JSON.stringify(input)}: norm_ gives ${JSON.stringify(got)}, the cases expect ${JSON.stringify(expected)}`);
  }
}
console.log(`${cases.length - bad}/${cases.length} cases match the Web App's norm_()`);
process.exit(bad ? 1 : 0);
