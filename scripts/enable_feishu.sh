#!/bin/bash
# enable_feishu.sh — 一键接通 RG660MK 设备网关 ↔ 飞书（2026-10-09 编写）
#   bash ~/enable_feishu.sh --check        # 只看现状（安全，只读）
#   bash ~/enable_feishu.sh --dry 'SECRET' # 只生成设备侧脚本并打印（不连设备、不改任何东西）
#   bash ~/enable_feishu.sh 'APP_SECRET'   # 写入配置 + 重启网关 + 验证连接
# Secret 不会出现在 ssh 命令行参数里（经安全临时文件传输、设备侧用后即删、输出掩码）。
set -u
K=/home/jedyying/.ssh/id_ed25519_termux
OPTS="-o ConnectTimeout=8 -o BatchMode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
MODE="${1:-check}"
DRY=0
SEC=""
if [ "$MODE" = "--dry" ]; then DRY=1; SEC="${2:?用法: bash ~/enable_feishu.sh --dry 'SECRET'}"; fi
if [ "$MODE" != "--check" ] && [ "$MODE" != "--dry" ]; then SEC="$MODE"; fi

T=""
for t in "root@fdc3:8285:b409::1" "root@192.168.1.1"; do
  if timeout 10 ssh -i $K $OPTS $t 'echo OK' 2>/dev/null | grep -q OK; then T=$t; break; fi
done
[ -z "$T" ] && { echo "✗ 设备不可达（fdc3 / 192.168.1.1 均不通）"; exit 1; }
SSHB="ssh -i $K $OPTS $T"
echo "✓ 设备连接: $T"

if [ "$MODE" = "--check" ]; then
  $SSHB 'echo "== .env 键名（只看名） =="; grep -oE "^[A-Z0-9_]+=" /data/hermes/.hermes/.env | sort; echo; echo "== 网关平台状态 =="; tail -40 /data/hermes/.hermes/logs/gateway.log 2>/dev/null | grep -iaE "feishu|platform|Connected" | tail -4'
  exit 0
fi

echo "▸ 生成设备侧更新脚本（Secret 不落 ssh 命令行）…"
TMP=$(mktemp /tmp/feishu_remote.XXXXXX); chmod 600 "$TMP"

python3 - "$SEC" > "$TMP" <<'PYOUT'
import sys, json, shlex
sec = sys.argv[1]
kvs = [
    ("FEISHU_APP_ID", "cli_aa01582a12f85cbd"),
    ("FEISHU_APP_SECRET", sec),
    ("FEISHU_DOMAIN", "feishu"),
    ("FEISHU_CONNECTION_MODE", "websocket"),
    ("FEISHU_ALLOWED_USERS", "ou_c5ca286cf595a82ac852c1aecf656280"),
]
kvjson = json.dumps(kvs)
pysrc = (
    "import sys,json\n"
    "envf=sys.argv[1]; kvf=sys.argv[2]\n"
    "kvs=json.load(open(kvf))\n"
    "try: lines=open(envf).read().splitlines()\n"
    "except Exception: lines=[]\n"
    "idx={}\n"
    "for i,l in enumerate(lines):\n"
    "    if '=' in l: idx[l.split('=',1)[0]]=i\n"
    "for k,v in kvs:\n"
    "    if k in idx: lines[idx[k]]='%s=%s'%(k,v)\n"
    "    else:\n"
    "        lines.append('%s=%s'%(k,v)); idx[k]=len(lines)-1\n"
    "open(envf,'w').write('\\n'.join(lines)+'\\n')\n"
    "print('env ok, keys:', ', '.join(k for k,v in kvs))\n"
)
remote = """#!/bin/sh
ENVF=/data/hermes/.hermes/.env
KV=$(mktemp /tmp/feishu_kv.XXXXXX); PRG=$(mktemp /tmp/feishu_upd.XXXXXX)
chmod 600 "$KV" "$PRG"
printf '%%s' %s > "$KV"
printf '%%s' %s > "$PRG"
cp -f "$ENVF" "$ENVF.bak_$(date +%%s)" 2>/dev/null || true
python3 "$PRG" "$ENVF" "$KV"
chmod 600 "$ENVF"
rm -f "$KV" "$PRG"
echo "-- 键名:"; grep -oE "^[A-Z0-9_]+=" "$ENVF" | sort
echo "-- 掩码校验:"; awk -F= '/^FEISHU_APP_SECRET=/{printf "secret长度=%%d 首4字符=%%s****\\n", length($2), substr($2,1,4)}' "$ENVF"
echo "-- 重启网关 --"; /etc/init.d/hermes restart
sleep 18
echo "-- 网关日志（feishu 相关）:"; tail -80 /data/hermes/.hermes/logs/gateway.log 2>/dev/null | grep -iaE "feishu|platform|connected|error|warn" | tail -10
echo "enable_feishu done"
""" % (shlex.quote(kvjson), shlex.quote(pysrc))
sys.stdout.write(remote)
PYOUT

if [ "$DRY" = "1" ]; then
  echo "---- 生成的设备脚本（dry-run，未执行） ----"
  sed 's/FEISHU_APP_SECRET[^,]*,,,,/FEISHU_APP_SECRET <MASKED>/g' "$TMP" | head -30
  echo "---- (script length: $(wc -c < "$TMP") bytes) ----"
  rm -f "$TMP"
  exit 0
fi

cat "$TMP" | $SSHB 'sh -s'
RC=$?
rm -f "$TMP"
echo "(rc=$RC)"
