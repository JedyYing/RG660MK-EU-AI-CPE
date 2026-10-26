// Minimal password-auth SSH runner for the MT200 link test.
// usage: node mt200-ssh.js "command to run"
const { Client } = require('ssh2');

const host = process.env.MT_HOST || '10.42.0.81';
const user = process.env.MT_USER || 'root';
const pass = process.env.MT_PASS;
if (!pass) {
  console.error('MT_PASS is not set. Export it first, e.g.  export MT_PASS="$(cat ~/.mt200-pass)"');
  process.exit(9);
}
const cmd = process.argv.slice(2).join(' ') || 'uname -a';

const conn = new Client();
let done = false;
const finish = (code, msg) => {
  if (done) return;
  done = true;
  if (msg) console.error('[ssh] ' + msg);
  conn.end();
  setTimeout(() => process.exit(code), 50);
};

conn.on('ready', () => {
  conn.exec(cmd, (err, stream) => {
    if (err) return finish(2, 'exec error: ' + err.message);
    let out = '';
    stream.on('close', (code, signal) => {
      process.stdout.write(out);
      finish(typeof code === 'number' ? code : 1, `channel closed code=${code} signal=${signal}`);
    });
    stream.on('data', (d) => { out += d.toString(); });
    stream.stderr.on('data', (d) => { out += d.toString(); });
  });
});
conn.on('error', (e) => finish(3, 'conn error: ' + (e.level ? e.level + ': ' : '') + e.message));
conn.on('close', () => finish(4, 'connection closed by peer'));
conn.on('keyboard-interactive', (n, i, l, prompts, cb) => cb(prompts.map(() => pass)));
conn.connect({
  host, port: 22, username: user, password: pass,
  tryKeyboard: true, readyTimeout: 20000,
  algorithms: {},
});
