// Compile the maintained JSX source with the pinned, vendored Babel build.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const babel = require(path.join(root, 'static/vendor/babel.js'));
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const output = babel.transform(source, { presets: ['react'], comments: false }).code;
fs.writeFileSync(path.join(root, 'static/app.compiled.js'), output + '\n');
console.log('Built static/app.compiled.js');
