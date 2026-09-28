#!/usr/bin/env bash
# Rebuild the vendored Vega bundles for Qt5's WebEngine.
#
# Qt 5.15 ships Chromium 83-87. Modern Vega/Vega-Lite builds use ES2021
# syntax (notably `??=`), which is a *parse* error on Chromium 83 - the whole
# bundle fails to load and the chart surface goes blank. This script lowers
# the syntax to what Chromium 83 accepts. Runtime APIs that Chromium 83/87
# lack (Object.hasOwn, structuredClone, ...) are shimmed separately by
# vendor/polyfills.js, which is loaded first in chart.html.
#
# Requires node + npm. Run from the repo root after updating any vega-* file:
#     ./tools/build_vendor.sh path/to/fresh/vega.min.js ...
set -euo pipefail

VENDOR="$(cd "$(dirname "$0")/.." && pwd)/explain_beaucoup/ui/web/vendor"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> installing babel into $WORK"
cd "$WORK"
npm init -y >/dev/null 2>&1
npm install --silent --no-fund --no-audit @babel/core @babel/cli @babel/preset-env acorn

cat > babel.config.json <<'JSON'
{
  "compact": true,
  "sourceType": "script",
  "presets": [["@babel/preset-env", {
    "targets": { "chrome": "83" },
    "bugfixes": true,
    "modules": false
  }]]
}
JSON

for f in vega.min.js vega-lite.min.js vega-embed.min.js; do
  echo "==> transpiling $f"
  ./node_modules/.bin/babel "$VENDOR/$f" -o "$WORK/$f"
  mv "$WORK/$f" "$VENDOR/$f"
done

echo "==> verifying no syntax beyond Chromium 83 survives"
cat > verify.js <<'JS'
const acorn = require("acorn"), fs = require("fs");
const dir = process.argv[2];
let bad = 0;
for (const f of ["polyfills.js","vega.min.js","vega-lite.min.js","vega-embed.min.js"]) {
  const s = fs.readFileSync(`${dir}/${f}`, "utf8");
  try { acorn.parse(s, { ecmaVersion: 2022, sourceType: "script" }); }
  catch (e) { console.log(`  FAIL parse ${f}: ${e.message}`); bad++; continue; }
  const logical = (s.match(/(\?\?|\|\||&&)=[^=]/g) || []).length;  // Chrome 85
  const statics = (s.match(/static\s*\{/g) || []).length;          // Chrome 94
  if (logical || statics) {
    console.log(`  FAIL ${f}: logical-assign=${logical} static-block=${statics}`); bad++;
  } else { console.log(`  OK   ${f}`); }
}
process.exit(bad ? 1 : 0);
JS
node verify.js "$VENDOR"
echo "==> vendor bundles are Chromium 83 clean"
