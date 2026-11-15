# M3：自己配对无线调试（移植计划）

日期：2026-11-05。参考实现已拉进 `docs/reference/aosp-pairing/`。

## 已确认的事实

| 项 | 结论 |
|---|---|
| 现成 Python 库 | ❌ `adb-shell 0.4.4` 只有 `connect`，**没有 `pair`**；PyPI 上没有配对的包 |
| AOSP 源码 | ✅ 可达（`pairing_auth.cpp` 302 行、`pairing_connection.cpp` 491 行） |
| 配对密码学 | **SPAKE2 over Curve25519**（BoringSSL `SPAKE2_CTX`），不是 P-256 |
| 角色名 | `"adb pair client"` / `"adb pair server"`（**含结尾 NUL**，`sizeof` 而非 `strlen`） |
| 后续加密 | `Aes128Gcm`，密钥来自 SPAKE2 输出 |
| 连接 | 先交换 `PairingPacketHeader`（版本字节），再 SPAKE2 消息，再 TLS，TLS 里交换加密的 peer info |

## 端口方案（Python，约 400 行）

```
phoneagent/devices/pairing/
├── spake2.py      包装 python-spake2 的 SPAKE2_A/B（Ed25519 群，同上）
├── transport.py   配对包的 framing：版本字节 + 长度前缀 + 读写
├── auth.py        SPAKE2 握手 + AES-128-GCM 加解密 peer info
└── tls.py         自签名客户端证书 + TLS 连接 + CNXN/AUTH
```

依赖：`spake2`（纯 Python）、`cryptography`（AES-GCM / TLS）。两者都能被 Chaquopy
pip 进来（`cryptography` 需要确认 Chaquopy 有 arm64 wheel）。

## 测试策略

1. **主机侧先跑通**：`adb forward tcp:38509 tcp:38509` 把手机的配对端口映射到本机，
   宿主 Python 用 `127.0.0.1:38509` + 配对码做握手。这样能快速迭代，不用每次装 APK。
2. **再进 APK**：同一份代码在 App 里直接连 `127.0.0.1:<port>`（无需 forward、无需 LAN）。
3. **最后接虚拟屏**：`SelfAdbDevice`（已写好骨架）替换 shell 来源。

## 约束与风险

- **配对窗口是有时限的**：弹窗一关，端口和配对码立即作废。每次测试都要重新打开弹窗、
  重新读码，所以代码要先在主机侧调到"一次成功"再上真机。
- **BoringSSL 与 python-spake2 的参数一致性**：两者的 M/N 点与哈希都来自同一份
  SPAKE2 草案，但需要实测确认；若不一致，只能自己实现 Montgomery ladder。
- **本机到手机 Wi-Fi 地址无路由**（`No route to host`，AP 隔离或代理 TUN），
  所以主机侧测试必须走 `adb forward`，不能直连 192.168.2.104。

## 建议

先做 `spake2.py` + `transport.py` + `auth.py`（主机侧），用 `adb forward` 打通配对；
`tls.py`（CNXN/AUTH + shell）放在第二步。整块预计 3–4 个工作日，不是一次会话能做完的。

---

## 决定：不引入 Mika 的代码（2026-11-05 修订）

一度把 Mika 的 8 个 `core/adb` 文件原样复制进来（只改包名），已经**全部撤回**。

理由：那是竞品项目的实现，注释、命名、设计决策都属于对方。Apache-2.0 允许这么用，
但在这个项目的语境里（自己的研究与产品），把它变成自己仓库的一部分不合适。

### 现在的路线：按 AOSP 规范自己实现

| 层 | 做法 | 依赖 |
|---|---|---|
| SPAKE2 | 用 PyPI 的 `spake2`（MIT，实现 IRTF 草案），**不读 Mika 的代码** | `spake2` |
| AES-128-GCM + TLS | `cryptography`（Apache-2.0/BSD） | `cryptography` |
| ADB 分帧 / CNXN / AUTH / shell | 按 `docs/reference/aosp-pairing/` 的 C++ 参考实现写 | 标准库 |

关键差异：AOSP 是**规范来源**（Android 平台自己的实现），Mika 只是它的一个客户端。
从规范实现，比复制竞品代码在归属上干净得多。

### 风险

- `cryptography` 在 Chaquopy 里是否有 arm64 wheel 需要先验证；没有的话退回到
  Kotlin + JCA（`AES/GCM/NoPadding`）实现 AES-GCM 部分，SPAKE2 仍可留 Python。
- SPAKE2 的 M/N 点与名字派生必须和 BoringSSL 一致，第一次在真机上握手就能验证。

---

## 方案 A 实施记录（2026-11-05 深夜）

### 已完成并用真机验证的部分

| 模块 | 验证方式 | 结果 |
|---|---|---|
| `ed25519.py` | RFC 8032 测试向量 | 公钥匹配 ✅ |
| `ed25519.py` | BoringSSL 注释里的 M/N 常量编解码往返 | 一致 ✅ |
| `spake25519.py` | 两端对称性 + 错码不同 | ✅ |
| 真机 | SPAKE2 消息被设备接受并回包 | ✅ |

`spake25519.py` 的移植要点（都来自 `docs/reference/aosp-pairing/spake25519.cc`）：

1. `x = 8 * (随机 512 位数 mod L)`
2. `w = SHA512(code) mod L`，再按低 3 位加 `L / 2L / 4L`（他们修 CVE 的"hack"）
3. 消息 = `x·B + w·M`（客户端）/ `+ w·N`（服务端）
4. 对端消息去掩码后再乘 `x`
5. transcript 每段都带 **8 字节小端长度前缀**，服务端视角的字段顺序与客户端**完全相同**
   （`their_name, my_name, their_msg, my_msg` —— 一度写反导致两端算不出同一个 key）

### 仍然卡住的唯一一点：ADB 的 AES 构造

- 加密后的 peer info 是 **8208 = 8192 + 16**，所以 **IV 不随包传输**
- 已知：`Aes128Gcm(key_material, len)`，密钥取前 16 字节
- 未知：IV 如何确定（零？计数器？从材料派生？）——需要 `crypto/aes_128_gcm.cpp`

抓取方式（这个路径模式已验证可行）：
`https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/crypto/include/adb/crypto/aes_128_gcm.h?format=TEXT`
（`pairing_connection.h` 就是这样在 `pairing_connection/include/adb/pairing/` 下找到的）

---

## 根因确认：密码里混入了 TLS 导出的密钥材料（2026-11-06 凌晨）

`pairing_connection.cpp:190-199`：

```cpp
// To ensure the connection is not stolen while we do the PAKE, append the
// exported key material from the tls connection to the password.
std::vector<uint8_t> exportedKeyMaterial = tls_->ExportKeyingMaterial(64);
pswd_.insert(pswd_.end(), exportedKeyMaterial.begin(), exportedKeyMaterial.end());
auth_ = CreatePairingAuthPtr(role_, pswd_);
```

也就是说 **SPAKE2 的密码 = 配对码 + 64 字节 TLS 导出密钥材料**（RFC 5705/8446 §7.5）。

### 为什么这解释了之前所有的失败

- 每次 TLS 会话导出的材料都不同 → 我们算出的 SPAKE2 密钥永远和手机不一样
- 所以 3 种 id 变体、4 种标量变体、6 种 AES 布局**全部** `InvalidTag`

### 为什么纯 Python 走不通

Python 的 `ssl` 模块**不暴露** keying material exporter。这正好印证了之前读 Mika 的
`AdbTls.kt` 时看到的那句注释：

> TLS 导出密钥(RFC 8446 §7.5)。JDK 没有公开接口，由平台提供：
> Android 上是 `SSLSockets.exportKeyingMaterial`

**结论：配对必须在 Kotlin/Java 里做**（走 Conscrypt 的平台 API），Python 只负责配对之后的
ADB 连接（客户端证书用 Python 的 `ssl` 就够）。

### 修订后的实施计划

1. 把已经写好并验证过的 Python 实现（`ed25519` / `spake25519` / `peerinfo` / `rsa` /
   `transport` / `auth`）**逐段移植到 Kotlin**——算法已定型，移植是机械工作
2. TLS 层用 `SSLSockets.exportKeyingMaterial` 取 64 字节，拼到密码后面
3. 配对成功后把 RSA 私钥交给 Python，用 `ssl` 客户端证书建 ADB 连接（CNXN/AUTH）
4. 参数（`kExportedKeySize = 64`）已确认；导出时的 label 还需从
   `tls_connection.cpp` 或 `tls_connection_boringssl.cpp` 里取

---

## Kotlin 实现的调试记录（2026-11-05 下午）

进度：**TCP → TLS → TLS 导出密钥 全部打通**，卡在第一个应用层数据包。

### 已排除

| 假设 | 验证方式 | 结论 |
|---|---|---|
| 证书格式不对 | 从 App dump DER，`openssl x509 -text` 解析 | ✅ 结构正确（v3 / SHA256-RSA / RSA2048） |
| 证书自签名无效 | `openssl verify -CAfile cert.pem cert.pem` | ✅ `OK` |
| 包头版本/类型/长度错 | 改成 `SPAKE2_MSG=1/PEER_INFO=2` 重试 | ❌ 仍是同一错误 |
| 需要 TLS 1.2 | 强制 `TLSv1.2` | ❌ 设备直接 `SSLHandshakeException`，说明只支持 1.3 |
| 端口过期 | 看门狗改为监听 logcat 事件并先清缓冲 | ✅ 不再有误导性的 ECONNREFUSED |

### 当前的精确现象

```
Pairing: TLS ready, code=6B exported=64B        (t+0ms)
Pairing: spake2 msg ready in 150ms size=32      (t+150ms)
Pairing: result ok=false ConnectionReset        (t+150ms，写在 socket 上失败)
```

设备侧只有一句 `Invalid PairingPacketHeader.`，**没有**版本/类型/长度任一具体原因，
对应 `ReadHeader` 里 `data.empty()` 那条静默分支 —— 即设备**一个字节都没读到**。

### 剩下的三个假设

1. **设备侧读超时极短**：握手完成后它立刻读，150ms 内没数据就关。可用
   「握手后立刻写一个假包头」来证伪；若设备报"点在曲线上"之类，说明它在等我们的数据。
2. **客户端证书实际没发出去**：TLS 1.3 的 CertificateRequest 需要
   `chooseClientAlias` 返回非空；我们的实现返回固定 "pa"，需要确认被调用。
3. **协议顺序**：也许客户端必须先读设备的消息。

### 关键代码位置

- `android-app/app/src/main/java/com/phoneagent/app/pairing/Pairing.kt` — 握手流程
- `.../Crypto.kt` — 曲线运算、SPAKE2、HKDF+AES、包封装
- `tools/pair_watch.py` — 自动抓配对窗口并触发（监听 logcat，先清缓冲）

---

## 决定性发现：我们自己的 App 会把配对弹窗顶掉（2026-11-05 14:30）

设备侧时间线：

```
14:30:07.843  Pairing server started on port 33573
14:30:07.846  mDNS advertising
14:30:08.422  Pairing failed          ← 0.58 秒后
14:30:08.422  Unregistered pairing service
14:30:08.445  我们的 App: ECONNREFUSED
```

而我们的取码方式是 `adb shell am start -n com.phoneagent.app/.MainActivity --es pair ...`
—— **把 App 拉到前台，配对弹窗随即被顶掉/关闭，配对服务跟着退出**。

这解释了之前所有忽好忽坏的现象：14:28:11 那次能连上（弹窗还在），
另几次落在服务已注销之后（ECONNREFUSED）。

### 修法

配对必须**在前台 UI 不出现的情况下**执行：

- 用 `am startservice` / `am broadcast` 触发一个 **Service**（或
  `BroadcastReceiver`）里跑 `Pairing.pair(...)`，屏幕停留在配对弹窗上；
- 或者在 App 内先点开弹窗（App 自己拉起 `ACTION_WIRELESS_DEBUGGING_PAIRING`），
  再由 App 直接连——Mika 就是这么做的（`PairingCoordinator`/`PairingScreen`）。

### 另一个已验证的结论

在 14:29:22 那次（弹窗仍在时）握手走到了：

```
header sent → spake2 ready → wrote packet → got packet type=0 size=32
```

即 **连接、TLS、导出、SPAKE2 发包、收包全部正常**。设备用 type=0 发 SPAKE2、
接受我们 type=1 的包头。只要解决"弹窗不被顶掉"，配对就能继续往下走。

---

## 当前状态（2026-11-05 15:00）

握手已经走到 **SPAKE2 交换完成**，设备在收到我们的 SPAKE2 之后静默关闭连接：

```
service: pairing 513074 @ 127.0.0.1:46011
chooseClientAlias keyTypes=EC,RSA
getCertificateChain alias=pa
exporter ... bytes=64
spake2 msg ready in 121ms
wrote SPAKE2 packet (type=1)
got packet type=0 size=32          ← 设备的 SPAKE2 收到了
Broken pipe                        ← 写 PEER_INFO 时对端已关闭
```

设备侧**没有任何** "Pairing failed" / "Unable to process their public key" 日志，
说明关闭是静默的。

### 已经解决的两个工程障碍（这是本轮最大价值）

1. **配对弹窗会被自己的 Activity 顶掉** → 改用**前台服务**触发，屏幕留在弹窗上
2. **前台服务需要权限** → `FOREGROUND_SERVICE` + `POST_NOTIFICATIONS`

在这之前所有"忽好忽坏"的 ECONNREFUSED 都是这两个原因，与协议无关。

### 下一步的候选假设

1. **SPAKE2 消息不被接受**：设备的 `x25519_ge_frombytes_vartime` 可能拒绝我们的点
   （虽然编码符合 RFC 8032）；可先用一个已知合法的点做对照实验。
2. **严格顺序**：设备可能要求"客户端先读、再写"，而我们抢先发了 SPAKE2。
3. **我们发的 type=1**：设备自己用 type=0；虽然它接受了我们的包头，但仍可能是
   让后续状态机错位的诱因（改成 0 试一次，代价极小）。

---

# ✅ 配对成功（2026-11-05 15:49）

```
got peer info type=1 size=8208
service result ok=true detail=配对成功，type=1 设备信息=adb-e3fa9e60-TNxoRb
```

设备把自己的 PeerInfo（8192 字节 + 16 字节 GCM tag）用协商出的密钥加密发来，
我们解密成功，设备侧同时也存下了我们的 RSA 公钥。**配对闭环。**

## 最终生效的参数组合（一个都不能少）

| 项 | 正确值 | 错误的代价 |
|---|---|---|
| 包类型 | **0 = SPAKE2，1 = PEER_INFO** | 设成 1/2 → 设备静默丢弃我们的包 |
| 口令拼装 | **配对码字节 + 64 字节导出材料（全程 ByteArray）** | 用 `decodeToString()` → 非 UTF-8 字节被替换，密钥全错 |
| SPAKE2 密钥材料 | **完整 64 字节 SHA-512 摘要** | 截断成 32 → HKDF 输入错，AES 解不开 |
| 导出标签 | **`"adb-label\0"`（10 字节，含 NUL）** | 用无 NUL 版本 → 口令不同 |
| TLS 版本 | **1.3** | 1.2 被设备直接拒绝握手 |
| 触发方式 | **前台 Service**（不是 Activity） | Activity 会把配对弹窗顶掉，服务随即退出 |
| 发包时机 | **算完再一次性发 header+payload** | 先发 header 再补 payload → 设备读 payload 超时 |

## 调试过程中最有价值的三个方法

1. **固定输入的交叉验证**：用确定的 `x` 和口令分别在 Python 与 Kotlin 里跑，
   逐字节比对。一次就证明"SPAKE2 移植没问题"，把嫌疑范围从整条链路收窄到输入参数。
2. **读参考实现的注释**：Mika 的 `AdbPairing.kt` 类注释直接写明了包类型 0/1、
   导出 64 字节、`adb-label\0`——三处都是我们猜错的。
3. **抓设备侧生命周期**：`Pairing server started on port` / `Pairing failed` /
   `Unregistered pairing service` 这几条日志，揭示了"弹窗被顶掉"这个真正的阻塞点。

## 下一步

配对拿到的是**设备信任的 RSA 公钥**。剩下的是 ADB 连接层用这把钥匙建 TLS 连接、
走 CNXN/AUTH，然后 `open("shell:...")` 拿 shell —— 这部分 Python 就能做，
`phoneagent/devices/selfadb.py` 的骨架已经在那里了。

---

## ADB 连接层：已到协议层，卡在 CNXN 无应答（2026-11-05 16:20）

### 已解决

| 问题 | 结论 |
|---|---|
| 端口是不是直接 TLS | ❌ 不是。发明文 CNXN 会收到 **STLS**，要先升级 |
| 升级后用什么 TLS 版本 | **必须 TLS 1.3**（1.2 会被握手阶段关闭） |
| 证书发不出去 | 用 Kotlin/Conscrypt（Python/OpenSSL 不发自签客户端证书） |
| soTimeout | 握手期间必须为 0，否则 TLS 失败 |

现象演进：

```
SSLHandshakeException: connection closed   →  握手被拒
SocketTimeoutException: Read timed out     →  握手成功，adbd 不回 CNXN
```

### 当前阻塞点

`AdbShell.kt` 的流程：

```kotlin
TCP connect
read(STLS)            // adbd 要求升级
SSLContext(TLS1.3, 客户端证书) 升级同一 socket
write(CNXN, "host::features=...")   // 已按 AOSP 客户端补全 features 列表
read(CNXN)            // ← 超时，设备没有任何回应（连 AUTH 都没有）
```

设备连 `AUTH` 都不发，说明它**既不认我们的连接、也不拒绝**——更像是它在等某个前置条件。

### 下一步（按优先级）

1. 抓 adbd 侧日志：`adb shell logcat -s adbd`（USB 还在，可以同时看两边）
2. 对比系统 `adb connect` 的行为：把 adbd 的无线端口 forward 到主机，
   `adb connect 127.0.0.1:<port>`，看它是否成功、耗时多少
3. 检查 CNXN 的 `A_VERSION`：AOSP 客户端发的是 `0x01000001`，
   但 `adb_secure` 路径上可能要求 `A_VERSION_MIN` 以上才继续

---

## 一个把自动化带偏的坑：两个端口别读错（2026-11-05 16:52）

无线调试页上有**两个不同的端口**：

| 端口 | 何时存在 | 说什么协议 |
|---|---|---|
| **配对端口**（弹窗里的 `IP 地址和端口`） | 只在「使用配对码配对设备」弹窗打开时 | 直接 TLS + SPAKE2 配对 |
| **ADB 连接端口**（设置页上的 `IP 地址和端口`） | 无线调试开启期间常驻 | 明文 CNXN → STLS → TLS |

我的自动化在**弹窗还开着**的时候去读地址，读到的是配对端口，然后拿 ADB 协议去连它
——结果就是 `SocketException: Connection reset`（配对服务不认识 CNXN）。

**读 ADB 端口必须在弹窗关闭后、从无线调试设置页读。**

## 到此刻为止的完整结论

> 本节是中间状态快照；最终结论见文末「M3 完成」一节。

```
配对：           ✅ 成功，设备已记录 phoneagent
TLS/STLS：       ✅ 完整实现（明文 CNXN → STLS → 回 STLS → TLS 1.3）
客户端证书：      ✅ 确实发出（chooseClientAlias → chain for pa）
adbd 认证：       ❌ CERTIFICATE_UNKNOWN —— 配对成功 3 毫秒后仍不认这把钥匙
```

最后一条是唯一未解的问题。已验证排除：证书格式、自签名、TLS 版本、STLS 流程、
密钥复用、报文长度。

剩下最可能的两条：
1. adbd 在启动时加载密钥列表，配对服务写入后需要 adbd 重启才生效
2. HyperOS 的配对记录与应用实际使用的密钥之间存在延迟或同步问题

---

## adbd 重启假设被证伪（2026-11-05 16:55）

流程：配对成功 → 关闭再打开无线调试（adbd 重启）→ 读**正确**的 ADB 端口（44063）→ 连接。

结果不变：

```
STLS acked → TLSv1.3 → chain for pa → SSLV3_ALERT_CERTIFICATE_UNKNOWN
```

所以不是"adbd 启动时才加载密钥"。设备里明明有 `phoneagent` 这条记录，adbd 依然不认。

### 现在最值得查的一条

**配对时设备存的是 PeerInfo 里的公钥，而 TLS 校验用的是证书里的公钥。**
两者都来自同一对 RSA 密钥，理论上一致——但我们的证书是**每次配对重新生成**的
（新序列号、新有效期），只有公钥相同。

要验证的话，下一步应该：
1. 从 `pair-cert.pem` 里解出公钥，和 `adbPublicKey()` 生成的那串做**逐字节比较**；
2. 如果一致，就说明问题在设备端如何匹配（可能它比较的是整张证书，而证书每次都变）。

第 2 条如果成立，解法就是把证书也持久化、永远不再重新签发——`loadOrCreateIdentity`
已经会复用证书，但 `selfSigned()` 在配对时仍会重新签发一张。

---

# M3 完成：ADB over TLS 全线打通（2026-11-05 17:34）

## 怎么把最后一段"黑盒"打开的

TLS 之后的所有帧都是加密的，从外面看不见。两个办法同时用：

**① 抓真实客户端的明文前奏。** 把设备 ADB 端口 forward 到本机，写一个透明 TCP 代理
（`/tmp/adbproxy.py`），再 `adb connect 127.0.0.1:<代理端口>`。代理在明文阶段逐帧解码，
看到 TLS ClientHello 就转为只计数：

```
C>D CNXN a0=0x01000001 a1=0x00100000 len=291   host::features=...
D>C STLS a0=0x01000000 a1=0x00000000 len=0
C>D STLS a0=0x01000000 a1=0x00000000 len=0     ← 客户端回的 STLS
C>D TLS ClientHello begins
```

**② 用 Python 复刻一个客户端。** 之前的结论"Python/OpenSSL 不发自签客户端证书"是
**错的**——那是被公钥截断的 bug 连累的误判。公钥修好后，Python 的 `ssl` 完全能发客户端
证书，于是可以逐帧打印 TLS 之后的流量（`/tmp/adbtls.py`，复用 Mac 的 `~/.android/adbkey`）。

真实流程第一次完整可见：

```
C>D CNXN (plain, 带 features)
D>C STLS  a0=0x01000000
C>D STLS ack
      TLS 1.3
D>C CNXN  a0=0x01000001 a1=0x00100000 len=377     ← 设备先说
C>D OPEN  shell,v2,raw:id                         ← 直接开服务，没有第二个 CNXN
D>C OKAY  a0=<remote id> a1=1
D>C WRTE  ...  (shell v2: 1 字节 id + 4 字节长度 + 负载)
D>C CLSE
```

## 根因：TLS 之后多发了一个 CNXN

`0x534c5453`（"STLS"）出现在 CNXN 之后，是因为**我们自己又发了一条 CNXN**。

adbd 把**任何** `A_CNXN` 都理解成"对端要开始 TLS 升级"，于是回一条 `STLS`。但此时
通道已经在 TLS 里了，TLS 握手不可能重来一遍 —— 连接就此僵死。

AOSP 的注释把这层语义写得很清楚（`adb/transport.cpp`，`FdConnection::DoTlsHandshake`）：

> `EnableClientPostHandshakeCheck(true)` … *"this assumes that, on handshake success,
> **the server speaks first**."*

所以正确顺序是：

1. 明文发 `CNXN`（**唯一一次**，它已经被升级前的握手消费掉了）
2. 收 `STLS`，回 `STLS`（`arg0 = A_STLS_VERSION = 0x01000000`）
3. TLS 1.3
4. **读设备主动发来的 `CNXN`**
5. 直接 `OPEN`

### 关于 `a0=0x01000001`

这个值属于**设备的 CNXN**，不属于 STLS：

| 帧 | cmd | arg0 | 含义 |
|---|---|---|---|
| 设备 STLS（明文阶段） | `0x534c5453` | `0x01000000` | `A_STLS_VERSION` |
| 设备 CNXN（TLS 之后先说） | `0x4e584e43` | `0x01000001` | `A_VERSION` |
| 二次 CNXN 触发的回复 | `0x534c5453` | `0x01000000` | 又一次升级请求 |

之前的日志把两条帧的字段看串了，才以为"STLS 带着 A_VERSION"。

## 实测结果

设备上、无电脑参与，App 连自己的 adbd：

```
STLS acked, upgrading to TLS
adb TLS TLSv1.3 alpn=null
device banner cmd=0x4e584e43 len=377
frame 0x59414b4f a0=4750 a1=1 len=0      OKAY
frame 0x45545257 a0=4750 a1=1 len=281    WRTE
frame 0x45534c43 a0=4750 a1=1 len=0      CLSE
connected via 192.168.2.104
SHELL uid=2000(shell) gid=2000(shell) groups=2000(shell),1004(input),1007(log),
      1011(adb),... context=u:r:shell:s0   exit=0
```

同时 Python 侧也用同一套流程验证通过（`phoneagent/devices/adbclient.py`：

```
stdout: 'hi'   stderr: 'oops'   exit: 7
```

## 实现要点（两个语言版本一致）

| 项 | 值 / 做法 |
|---|---|
| 明文 CNXN | `arg0=A_VERSION(0x01000001)`, `arg1=1MiB`, 负载 `host::features=<列表>\0` |
| STLS 回执 | `arg0=A_STLS_VERSION(0x01000000)`, `arg1=0` |
| TLS | 必须 1.3；握手期间 socket 超时必须取消；ALPN `adb`（设备不选也不影响） |
| TLS 后 | **不要发 CNXN**；先读设备 banner |
| 服务 | `OPEN arg0=<local id> arg1=0 data="shell,v2,raw:<cmd>\0"` |
| 回执 | `OKAY arg0=<local id> arg1=<remote id>`，remote id 来自 OKAY/WRTE 的 `arg0` |
| shell v2 | 每个 WRTE 内是 `[id:1][len:4 LE][payload]`，id 1=stdout 2=stderr 3=退出码（**退出码是负载本身，1 字节，不是长度**） |

## 顺带修掉的错误认知

- ~~Python/OpenSSL 发不出自签客户端证书~~ → 能发。之前的失败是公钥 blob 被截断导致
  `CERTIFICATE_UNKNOWN`，和 OpenSSL 无关。
- ~~TLS 后要补发 CNXN 确认~~ → 不要补，补了就死。
- 退出码解析：`id==3` 的包长度恒为 1，真正的退出码在负载的第 0 个字节。

