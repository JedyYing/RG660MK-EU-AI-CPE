# RG660MK Matter Network Infrastructure Manager 部署报告

**日期**：2026-09-02
**执行**：QRIBuddy(自主执行,用户已下班)
**报告目录**：`~/Downloads/RG660MK_MATTER_DEPLOY_20260901_2010`

---

## 结论(先读这段)

`matter-netman-mbedtls` 已在本机**交叉编译成功**,生成可用的 `.ipk`,依赖库齐备,SHA256 已记录。**尚未安装到设备、未启动、未做功能验证**——因为 RG660MK 当前物理不可达(无 ADB、无串口、网络无该设备)。设备一旦接上,按第九节命令即可完成安装与验证。

当前阶段界定:**仅编译成功**。未达"已安装 / 进程已启动 / 可发现可配网 / Thread BR 完整功能"任一阶段。

---

## 1. RG660MK 设备信息

**未采集到**——设备当前不可达:`adb devices` 为空、无 `/dev/ttyUSB*`/`/dev/ttyACM*` 串口、局域网邻居中无 RG660MK。设备型号、固件、内核、opkg 架构均待设备接入后现场采集(命令见第十节)。

目标架构从 SDK 侧推定为 `aarch64_cortex-a55_neon-vfpv4`(见第 4 节 ipk 架构),但**须以设备 `opkg print-architecture` 现场核对**后方可安装。

## 2. 使用的 SDK 与 target

- **SDK 根目录**:`/var/tmp/RG660MK_build/T930/openwrt`(由 `~/Downloads/RG660MK_SDK.tar.zst` 解压,MediaTek MT6988 · QuecOpen OpenWrt 23.05)。
- **SDK 完整性**:`Makefile`、`scripts/feeds`、`include/toplevel.mk`、`target/linux/gem6xxx/` 四项齐全,是可编译的完整 buildroot。
- **target**:`evb6988_cpe_mt7992_emmc`。

## 3. target 选择证据(7992 vs 7990)

参考 ReadME 写的是 `evb6988_cpe_mt7990_nand`,**不予采信**。SDK 侧证据一致指向 **7992_emmc**:

- 厂商构建入口 `quecopen_projects` 中**唯一合法项目组合**:
  `RG660MKEU00AA,RG660ENDC,evb6988_cpe_mt7992_emmc,...`
- 该 SDK 固件包 vendor_info、既往固件产物、编译手册全部为 7992_emmc。
- target 目录虽有 5 个候选(7990/7992 × emmc/nand),但厂商脚本只放行 7992_emmc。

**缺口**:brief 要求的"设备 board.json 与 SDK 双向印证"只完成了 SDK 单边;设备侧 `ubus call system board` 未采集。安装前请现场用设备 board.json 复核。

## 4. 产物(.ipk)与 SHA256

位于 `~/Downloads/RG660MK_MATTER_DEPLOY_20260901_2010/ipk/`:

| 包 | 大小 | 说明 |
|---|---|---|
| matter-netman-mbedtls_2025-10-29-8f221d80-1 | 559 KB | 主交付物 |
| libmbedtls12_2.28.4-1 | 261 KB | **重编版,含 CCM/HKDF(关键)** |
| jsonfilter_2018-02-04-c7e938d6-1 | 12 KB | 依赖 |
| libubus20230605 / libubox20230523 | 13/26 KB | 依赖(设备多半自带) |

```
bca6a6d523bc4dbf40188f61a980d2d0aa2a5e7cfe46d6bedea8510173680fa2  matter-netman-mbedtls_2025-10-29-8f221d80-1_aarch64_cortex-a55_neon-vfpv4.ipk
1696e8b2b4ad489c4721917042c12f1ad9e6427930aa0f89a6b166995eef5c45  libmbedtls12_2.28.4-1_aarch64_cortex-a55_neon-vfpv4.ipk
10906d76cdb9ca5660c0c153942149e558439015ea0a2ad6bc866501f4cce2fd  jsonfilter_2018-02-04-c7e938d6-1_aarch64_cortex-a55_neon-vfpv4.ipk
5833176a091bcaa6f81db26caad92ebdfd46bbce0634eb07816a5f49423e94ec  libubox20230523_2023-05-23-75a3b870-1_aarch64_cortex-a55_neon-vfpv4.ipk
6185e77601e2d79dc4470e98c43da9f424134e839212508696f4de83353df4ec  libubus20230605_2023-06-05-f787c97b-1_aarch64_cortex-a55_neon-vfpv4.ipk
```

**ipk 内含**:`/usr/sbin/matter-network-manager-app`(主程序)、`/etc/init.d/matter`(procd 启动脚本,START=90,matter 用户运行,崩溃 respawn)、`/usr/share/matter/bootstrap.sh`、`/usr/share/acl.d/matter_acl.json`。
**未生成固件**——按 brief 走 opkg 路线,未做 sysupgrade/刷机。

## 5. matter feed 与来源

- **matter.tar.gz**(厂商提供的 matter-openwrt feed,非源码):
  SHA256 `550842c20b16648792e99e4732be41d8265add9a83f104b44ca4fdbfd851df23`
  确认含 `service/matter-netman/Makefile`,是 feed 无误。
- feed 版本 matter-netman commit `8f221d80`(2025-10-29)。
- **connectedhomeip 源码**:用户已手动 clone 到 `~/Downloads/connectedhomeip`(2.0G,含 pigweed/jsoncpp/nlassert/nlio 子模块),通过 feed 官方 `USE_SOURCE_DIR` 开关引用,**未放进 feeds/matter**(遵守红线)。

## 6. 修改的文件(均有备份)

1. **target.config**(`.../gem6xxx/evb6988_cpe_mt7992_emmc/`):末尾加 4 行 matter 配置。备份 `target.config.bak`。
2. **feeds.conf**:改用本地 `src-link matter ../../feeds/matter`(非 GitHub src-git)。
3. **feeds/matter/devel/gn/Makefile**:`PKG_MIRROR_HASH` 对齐本地 gn 源码文件,复用用户提供的 `gn-2025-03-21-6e8e0d6d.tar.xz`。
4. **feeds/matter/service/matter-netman/Makefile**:启用 `USE_SOURCE_DIR` 指向本地 connectedhomeip。备份 `Makefile.bak`。
5. **connectedhomeip 源码**:手动应用 feed 的 2 个补丁(`010-zap-disable-arl`、`020-dont-overwrite-factory-config`),改动 `network-manager-app.zap`、`PosixConfig.cpp`(git 可还原)。
6. **libmbedtls**:强制 clean 重编,使 CCM/HKDF 生效(见第 8 节)。

## 7. 关键配置项

`.config` 最终确认:

```
CONFIG_PACKAGE_matter-netman-mbedtls=y
CONFIG_MBEDTLS_CCM_C=y
CONFIG_MBEDTLS_HKDF_C=y
```

注:`CONFIG_PACKAGE_python3=y` 为厂商文档要求,已写入 target.config;但 Matter 代码生成用的是 **host 端** python,target 端 python3 是否最终打包由 defconfig 依据依赖决定,不影响本包编译。设备端若无需 python3 可省,以节省固件空间。

## 8. 编译过程与踩坑(首个真实错误链)

编译分三轮,每轮暴露并解决一个真实错误:

1. **python3-host-ssl 缺 python3-host.mk**:早前 `feeds clean` 误伤 packages feed 索引所致,重新 `feeds update packages` 修复。
2. **链接失败 `undefined reference to mbedtls_hkdf / mbedtls_ccm_*`**:根因是 libmbedtls 在加配置**之前**已编成、缓存了不含 CCM/HKDF 的旧库(config.h 里两项为 `//#define` 注释态)。**解法**:`make package/libs/mbedtls/clean && compile` 强制重编,新库 config.h 两项转为生效,`libmbedcrypto.a` 出现 `mbedtls_hkdf`/`mbedtls_ccm_setkey` 符号。
3. **重链 matter-netman**:`clean` 后重编,链接通过,`MATTER_V3_EXIT=0`,ipk 生成。

- gn 主机工具:用用户本地 gn 源码编成(`staging_dir/hostpkg/bin/gn`),绕开拉不通 Google 存储站的老问题。
- 构建环境:系统 Python 3.10.12,gcc 11.4.0,交叉工具链 aarch64-openwrt-linux-musl gcc 12.3.0。
- 完整日志:`matter_build_v3.log`(成功)、`matter_build_v2.log`(链接错误)、`mbedtls_rebuild.log`。

## 9. 安装方案(待设备接入,勿盲跑)

**前置校验**(全部满足才装,任一不符即停):

- 设备 `opkg print-architecture` 接受 `aarch64_cortex-a55_neon-vfpv4`。
- 设备 board.json 确认为 7992_emmc,与 SDK 一致。
- **重点**:设备现有 `libmbedtls12` 版本。若设备已装 `2.28.4-1`(与本次同版本号),opkg 会认为"已装"而跳过,导致设备仍用**不含 CCM/HKDF 的旧库**,matter 进程会因缺符号无法运行。此时**不可** `--force-*`;正确做法是确认设备库确实含 CCM/HKDF(`strings /usr/lib/libmbedcrypto.* | grep mbedtls_hkdf`),不含则需正规升级该库(评估对其他依赖 mbedtls 组件的影响)后再装。

**安装命令**(经 ADB/SSH 推包到设备 `/tmp/matter-deploy/`,两端校验 SHA256 后):

```sh
# 先 dry-run(若支持)
opkg install --noaction /tmp/matter-deploy/matter-netman-mbedtls_*.ipk
# 依赖 → 主包,均不用任何 --force-*
opkg install /tmp/matter-deploy/jsonfilter_*.ipk
opkg install /tmp/matter-deploy/libmbedtls12_*.ipk   # 仅当设备库确需更新
opkg install /tmp/matter-deploy/matter-netman-mbedtls_*.ipk
```

若只能靠新固件部署(opkg 因 ABI 不可行),**停止**,不自行刷机——另出刷机方案交用户确认。

## 10. 启动与验证(待设备接入)

```sh
/etc/init.d/matter enable && /etc/init.d/matter restart
sleep 30
pgrep -af matter-network-manager-app          # 确认进程未反复重启
logread | grep -iE 'matter|chip|commission|fatal|error' | tail -100
ss -lunp | grep -E ':5540|:5353'              # Matter/mDNS 端口
ls -l /etc/matter; test -s /etc/matter/chip_factory.ini
ubus list | grep -i otbr                       # Thread BR 能力
ls /sys/class/bluetooth                         # BLE 能力
```

注:`/etc/matter/chip_factory.ini` 含 commissioning discriminator 与 PIN,**勿写入公开日志**,用户本地 `cat` 查看即可。

## 11. 能力矩阵(当前)

| 能力 | 状态 | 说明 |
|---|---|---|
| 交叉编译 matter-netman-mbedtls | **PASS** | ipk 已生成,SHA256 已记录 |
| ipk 安装到设备 | **未验证** | 设备不可达 |
| Matter 进程启动 | **未验证** | 同上 |
| BLE 配网 | **未验证 / 需注意** | 该官方构建默认关闭 BLE,见下 |
| Wi-Fi Matter 可发现/可配网 | **未验证** | 需设备端实测 |
| Thread / OTBR 完整功能 | **未验证 / 很可能不具备** | 需 Thread RCP 硬件与 otbr,RG660MK 作为 Wi-Fi CPE 多半无此硬件 |

## 12. 重要性质提醒

- **这是 uncertified 开发构建**:使用 CSA 测试 VID/PID(0xFFF1/0x8013)与 SDK 公开的开发证书,适合验证 Matter 配网/控制流程,**不等于 Matter 认证产品**,不可直接出货。
- **matter-netman = Network Infrastructure Manager**,不是通用 chip-tool。它把路由器作为 Matter 网络基础设施节点接入生态。你的目标是"CPE 帮智能家居经 Wi-Fi 入网并控制",该包提供的是网络侧基础设施角色;若实测发现它不覆盖"主动配网+控制第三方设备",可能还需 chip-tool 或 Controller SDK,届时另议。
- 官方 feed 最新版主要面向 OpenWrt 25.12.x;本次用的是厂商随 SDK 提供的 matter.tar.gz(适配 23.05),未盲用 GitHub 最新版。

## 13. 下一步(仅最必要)

1. 接入 RG660MK(ADB 或 SSH),采集 board.json、`opkg print-architecture`、现有 libmbedtls 版本及其 CCM/HKDF 符号。
2. 按第 9 节前置校验逐条核对,通过后按命令装包。
3. 按第 10 节启动并观察 30 秒稳定性;明确区分"进程起来"与"配网/控制功能可用"。
4. 若需 BLE 配网,确认该构建的 BLE 开关与设备蓝牙栈,可能要调整 feed 配置重编。
