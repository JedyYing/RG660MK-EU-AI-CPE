// Upload a local file to MT200 via SFTP.
// usage: node mt200-put.js <local-path> <remote-path>
const { Client } = require('ssh2');

function requirePass() {
  const p = process.env.MT_PASS;
  if (!p) {
    console.error('MT_PASS is not set. Export it first, e.g.  export MT_PASS="$(cat ~/.mt200-pass)"');
    process.exit(9);
  }
  return p;
}
const fs = require('fs');

const [, , local, remote] = process.argv;
if (!local || !remote) { console.error('usage: node mt200-put.js <local> <remote>'); process.exit(2); }

const conn = new Client();
conn.on('ready', () => {
  conn.sftp((err, sftp) => {
    if (err) { console.error('sftp error', err.message); process.exit(3); }
    sftp.fastPut(local, remote, { concurrency: 16, chunkSize: 32768 }, (e) => {
      if (e) { console.error('put error', e.message); process.exit(4); }
      sftp.stat(remote, (e2, st) => {
        console.log(`uploaded ${remote} ${st ? st.size : '?'} bytes`);
        conn.end();
        process.exit(0);
      });
    });
  });
});
conn.on('error', (e) => { console.error('conn error', e.message); process.exit(5); });
conn.connect({ host: process.env.MT_HOST || '10.42.0.81', port: 22, username: 'root',
               password: requirePass(), readyTimeout: 20000 });
