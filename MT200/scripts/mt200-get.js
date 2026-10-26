// Download a file from MT200 via SFTP.
// usage: node mt200-get.js <remote-path> <local-path>
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
const path = require('path');

const [, , remote, local] = process.argv;
if (!remote || !local) { console.error('usage: node mt200-get.js <remote> <local>'); process.exit(2); }

const conn = new Client();
conn.on('ready', () => {
  conn.sftp((err, sftp) => {
    if (err) { console.error('sftp error', err.message); process.exit(3); }
    sftp.stat(remote, (e, st) => {
      if (e) { console.error('stat error', e.message); process.exit(4); }
      const total = st.size;
      const tmp = local + '.part';
      let lastPct = -1;
      const t0 = Date.now();
      const ws = fs.createWriteStream(tmp);
      const rs = sftp.createReadStream(remote, { highWaterMark: 1 << 20 });
      rs.on('data', (d) => {
        ws.write(d);
        const pct = Math.floor((rs.bytesRead / total) * 100);
        if (pct >= lastPct + 5) {
          lastPct = pct;
          const mbps = rs.bytesRead / 1e6 / ((Date.now() - t0) / 1000);
          console.log(`${pct}% ${(rs.bytesRead / 1e6).toFixed(0)}/${(total / 1e6).toFixed(0)} MB @ ${mbps.toFixed(1)} MB/s`);
        }
      });
      rs.on('error', (e2) => { console.error('read error', e2.message); process.exit(5); });
      ws.on('error', (e2) => { console.error('write error', e2.message); process.exit(6); });
      ws.on('finish', () => {
        fs.renameSync(tmp, local);
        const dt = (Date.now() - t0) / 1000;
        console.log(`DONE ${local} ${fs.statSync(local).size} bytes in ${dt.toFixed(1)}s`);
        conn.end();
        process.exit(0);
      });
      rs.on('end', () => ws.end());
    });
  });
});
conn.on('error', (e) => { console.error('conn error', e.message); process.exit(7); });
conn.connect({
  host: process.env.MT_HOST || '10.42.0.81',
  port: 22,
  username: 'root',
  password: requirePass(),
  readyTimeout: 20000,
});
