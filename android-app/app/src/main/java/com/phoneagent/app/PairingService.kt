package com.phoneagent.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import com.phoneagent.app.pairing.Pairing

/**
 * Runs a pairing attempt without touching the screen.
 *
 * This matters: pairing only works while the system dialog is up, and starting
 * an Activity brings it to the foreground and dismisses that dialog, which
 * tears the pairing server down mid-flight. A Service leaves it alone.
 *
 *   adb shell am startservice -n com.phoneagent.app/.PairingService \
 *       --es pair "123456@127.0.0.1:38509"
 */
class PairingService : Service() {

    private fun wifiAddress(): String? =
        java.net.NetworkInterface.getNetworkInterfaces().toList()
            .asSequence()
            .filter { it.name.startsWith("wlan") && it.isUp }
            .flatMap { it.inetAddresses.toList().asSequence() }
            .filterIsInstance<java.net.Inet4Address>()
            .firstOrNull { !it.isLoopbackAddress }
            ?.hostAddress

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Chaquopy needs an Android platform before any module can be imported
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(applicationContext))
        }
        val adbPort = intent?.getIntExtra("adbport", 0) ?: 0
        val probePort = adbPort
        // probe-only mode; when a pairing request is present, pair first and
        // probe from inside that branch instead
        if (adbPort > 0 && intent?.getStringExtra("pair").isNullOrBlank()) {
            Thread {
                try {
                    // Kotlin, not Python: Conscrypt sends our self-signed
                    // client certificate, OpenSSL does not
                    val dir = java.io.File(filesDir, "identity")
                    val cert = java.io.File(dir, "pair-cert.pem").readBytes()
                    val key = java.io.File(dir, "pair-key.pem").readBytes()
                    // adbd advertises on the Wi-Fi interface; loopback may not
                    // be listening, so try both and report whichever answered
                    var last: Throwable? = null
                    var res: String? = null
                    for (host in listOf(wifiAddress(), "127.0.0.1")) {
                        if (host == null) continue
                        try {
                            res = com.phoneagent.app.pairing.AdbShell.shell(cert, key, host, adbPort, "id")
                            Log.i("Pairing", "connected via $host")
                            break
                        } catch (t: Throwable) {
                            last = t
                            Log.i("Pairing", "via $host failed: ${t.javaClass.simpleName}: ${t.message}")
                        }
                    }
                    if (res == null) throw last ?: IllegalStateException("no address")
                    Log.i("Pairing", "SHELL " + res.replace("\n", " | "))
                } catch (t: Throwable) {
                    Log.i("Pairing", "shell probe failed: ${t.javaClass.simpleName}: ${t.message}")
                } finally {
                    stopSelf()
                }
            }.start()
            return START_NOT_STICKY
        }
        if (intent?.getBooleanExtra("selftest", false) == true) {
            Log.i("Pairing", "SELFTEST " + com.phoneagent.app.pairing.SelfTest.run())
            stopSelf()
            return START_NOT_STICKY
        }
        val watch = intent?.getBooleanExtra("watch", false) == true
        val arg = intent?.getStringExtra("pair") ?: ""
        if (!watch && !arg.contains(":")) {
            Log.i("Pairing", "service: nothing to do")
            stopSelf()
            return START_NOT_STICKY
        }
        // adb cannot start a background service; a foreground one is allowed
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel("pairing", Tr.t("配对"), NotificationManager.IMPORTANCE_LOW)
        )
        startForeground(
            1,
            Notification.Builder(this, "pairing")
                .setContentTitle(Tr.t("PhoneAgent 正在配对"))
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .build()
        )

        // Watch mode: the user is on the pairing dialog, and we read the code
        // off the screen ourselves. The dialog's pairing server dies as soon
        // as it is dismissed, so the app cannot ask the user to retype it.
        if (watch) {
            Thread {
                val deadline = System.currentTimeMillis() + WATCH_MS
                var detail = Tr.t("没有检测到配对弹窗（请在无线调试里打开「使用配对码配对设备」）")
                try {
                    while (System.currentTimeMillis() < deadline) {
                        val found = PhoneAgentAccessibilityService.scanPairingDialog()
                        if (found.isNotEmpty()) {
                            Log.i("Pairing", "watch: read $found from the dialog")
                            val result = pairBlocking(found)
                            detail = result.detail
                            if (result.ok) {
                                // the user is still in Settings; bring the
                                // agent up now so nothing else has to happen
                                startForegroundService(
                                    Intent(this@PairingService, AgentService::class.java)
                                )
                                detail += Tr.t("，正在启动 agent")
                            }
                            break
                        }
                        Thread.sleep(700)
                    }
                } catch (t: Throwable) {
                    detail = "${t.javaClass.simpleName}: ${t.message}"
                }
                Log.i("Pairing", "watch result: $detail")
                nm.notify(
                    1,
                    Notification.Builder(this, "pairing")
                        .setContentTitle(Tr.t("PhoneAgent 配对结果"))
                        .setContentText(detail)
                        .setSmallIcon(android.R.drawable.stat_notify_sync)
                        .build()
                )
                stopSelf()
            }.start()
            return START_NOT_STICKY
        }

        Thread {
            try {
                Log.i("Pairing", "service: pairing $arg")
                pairBlocking(arg)
            } catch (t: Throwable) {
                Log.i("Pairing", "service failed: ${t.javaClass.simpleName}: ${t.message}")
            } finally {
                stopSelf()
            }
        }.start()
        return START_NOT_STICKY
    }

    /** Shared by the explicit-code path and the watch path. */
    private fun pairBlocking(arg: String): Pairing.Result {
        var rest = arg
        var host = "127.0.0.1"
        if (arg.contains("@")) {
            rest = arg.substring(arg.indexOf('@') + 1)
            host = rest.substring(0, rest.lastIndexOf(':'))
            rest = arg.substring(0, arg.indexOf('@')) + rest.substring(rest.lastIndexOf(':'))
        }
        val code = rest.substring(0, rest.lastIndexOf(':'))
        val port = rest.substring(rest.lastIndexOf(':') + 1).toInt()
        Log.i("Pairing", "pairing $code @ $host:$port")
        val r = Pairing.pair(
            host, port, code, null, "phoneagent", 20000,
            java.io.File(filesDir, "identity").absolutePath,
        )
        Log.i("Pairing", "service result ok=${r.ok} detail=${r.detail}")
        return r
    }

    private companion object {
        /**
         * The system dialog's code is valid for about two minutes; matching
         * that keeps a forgotten watcher from lingering in the shade.
         */
        private const val WATCH_MS = 150_000L
    }
}
