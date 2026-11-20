package com.phoneagent.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * Brings the on-device stack up without a computer anywhere in the loop:
 *
 *   mDNS/scanner finds the adbd port -> the paired key opens TLS to it -> adbd
 *   gives us a shell -> the helper asset is pushed and started -> a virtual
 *   display exists -> the agent's HTTP server runs inside this process.
 *
 *   adb shell am start-foreground-service -n com.phoneagent.app/.AgentService \
 *       [--ei adbport 44063] [--es app com.android.settings] \
 *       [--es mock "tap(0.5,0.6);finish(done)"]
 *
 * The LLM key is read from filesDir/llm.json ({"api_key","api_base","model"}).
 */
class AgentService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private val WAKE_LOCK_MS = WATCH_LOCK_MS

    /**
     * Keep the process out of HyperOS's freezer.
     *
     * A backgrounded app is frozen within about thirty seconds even while it
     * holds a foreground service: `cgroup.freeze` goes to 1, every thread lands
     * in `do_freezer_trap`, and the task server keeps its socket open while
     * answering nothing. Holding this lock kept `cgroup.freeze` at 0 for over
     * three minutes behind the launcher, and an eleven-step task ran to
     * completion with the app in the background.
     *
     * The AOSP knobs do not help here (measured): cached_apps_freezer
     * disabled, deviceidle whitelist, and a standby bucket of EXEMPTED all
     * still froze.
     */
    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = power.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "PhoneAgent::agent"
            // Timed rather than open-ended: thirty minutes is longer than any
            // task's step budget, and a lock that is never released is a battery
            // bug if the service is ever killed without onDestroy running.
        ).also { it.setReferenceCounted(false); it.acquire(WAKE_LOCK_MS) }
        Log.i("Pairing", "agent: wake lock held")
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    override fun onDestroy() {
        releaseWakeLock()
        // The flag lives on the companion object and outlives this instance. Left
        // set, a restarted service believes the agent is already running, skips
        // Python and the display, and answers nothing -- a "connected" app with a
        // dead server behind it.
        started = false
        ready = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel("agent", "PhoneGo", NotificationManager.IMPORTANCE_LOW)
        )
        startForeground(
            2,
            Notification.Builder(this, "agent")
                .setContentTitle(Tr.t("PhoneGo 正在启动"))
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .build()
        )

        acquireWakeLock()

        // a second intent must not start a second display or a second server
        if (started) {
            val tap = intent?.getStringExtra("tap") ?: ""
            if (tap.contains(",")) driveExisting(tap)
            val label = intent?.getStringExtra("taptext") ?: ""
            if (label.isNotEmpty()) clickExisting(label)
            val typed = intent?.getStringExtra("settext") ?: ""
            if (typed.isNotEmpty()) typeExisting(typed)
            val scroll = intent?.getStringExtra("scroll") ?: ""
            if (scroll.isNotEmpty()) scrollExisting(scroll)
            val gesture = intent?.getStringExtra("gesture") ?: ""
            if (gesture.contains(",")) gestureExisting(gesture)
            if (intent?.getBooleanExtra("dump", false) == true) dumpExisting()
            return START_STICKY
        }
        started = true

        val explicitPort = intent?.getIntExtra("adbport", 0) ?: 0
        val app = intent?.getStringExtra("app") ?: ""
        // filesDir/agent.json is the durable way in: HyperOS kills a service
        // that starts with no visible activity, so tests usually come through
        // MainActivity, which cannot pass extras to the service it starts
        val config = AgentConfig.load(this)
        val mock = intent?.getStringExtra("mock")?.takeIf { it.isNotEmpty() }
            ?: config.mock
        val tapAt = intent?.getStringExtra("tap") ?: ""
        val dumpTree = intent?.getBooleanExtra("dump", false) ?: false
        val tapText = intent?.getStringExtra("taptext") ?: ""

        Thread {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val port = explicitPort.takeIf { it > 0 }
                    ?: AdbPortScanner.discover(this)
                    ?: throw IllegalStateException(
                        Tr.t("无线调试未开启：adbd 没有监听端口。请到「设置 → 开发者选项 → 无线调试」打开")
                    )
                Log.i("Pairing", "agent: adbd port $port")

                val helper = extractHelper()
                val maps = extractMaps()
                val identity = File(filesDir, "identity").absolutePath
                val py = Python.getInstance()
                val ondevice: PyObject = py.getModule("phoneagent.ondevice")
                // Ask first whether this Android build can host a display at
                // all. Cheaper than a failed start, and it names the reason.
                val probe = ondevice.callAttr(
                    "probe_display", identity, port, helper.absolutePath,
                ).toString()
                Log.i("Pairing", "agent: $probe")
                if (!probe.startsWith("PROBE ok")) {
                    val reason = probe.removePrefix("PROBE error: ")
                    Log.w("Pairing", "agent: cannot host a display: $reason")
                    nm.notify(
                        2,
                        Notification.Builder(this, "agent")
                            .setContentTitle(Tr.t("PhoneGo 无法在此设备上运行"))
                            .setContentText(reason)
                            .setStyle(Notification.BigTextStyle().bigText(reason))
                            .setSmallIcon(android.R.drawable.stat_notify_error)
                            .build()
                    )
                    stopSelf()
                    return@Thread
                }

                // The virtual display should match the phone it runs on, not a
                // constant. resources.displayMetrics is the *window* size (it
                // excludes the system bars: 1280x2568 here), so the real display
                // has to come from maximumWindowMetrics.
                val windowManager = getSystemService(android.view.WindowManager::class.java)
                val bounds = windowManager.maximumWindowMetrics.bounds
                val dpi = resources.displayMetrics.densityDpi
                Log.i("Pairing", "agent: display ${bounds.width()}x${bounds.height()} @${dpi}dpi")
                val result = ondevice.callAttr(
                    "start_display", identity, port, helper.absolutePath,
                    bounds.width(), bounds.height(), dpi, app,
                ).toString()
                Log.i("Pairing", "agent: $result")

                // capture the tree while the display is fresh: that is when an
                // accessibility root is available, if it ever is
                runCatching {
                    val id = Regex("display=(\\d+)").find(result)?.groupValues?.get(1)?.toInt() ?: 0
                    val tree = PhoneAgentAccessibilityService.dump(id)
                    File(filesDir, "tree-startup.xml").writeText(tree)
                    Log.i("Pairing", "agent: startup tree ${tree.length} bytes for display $id")
                }

                // Before Python starts: the agent reads this file and requires
                // the token on every request, because any app on the phone can
                // reach loopback.
                AgentToken.ensure(this@AgentService)

                // the chat UI talks to this over plain HTTP on loopback
                val served = ondevice.callAttr(
                    "serve",
                    SERVER_PORT,
                    config.apiKey,
                    config.apiBase,
                    config.model,
                    config.visionModel,
                    mock,
                    maps.absolutePath,
                    File(filesDir, "memory.json").absolutePath,
                    "",
                    config.maxRounds,
                ).toString()
                Log.i("Pairing", "agent: $served")
                if (served.startsWith("listening")) {
                    ready = true
                }

                val shot = File(filesDir, "frame.png").absolutePath
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", shot).toString())

                if (tapAt.contains(",")) {
                    val (x, y) = tapAt.split(",").map { it.trim().toInt() }
                    Log.i("Pairing", "agent: " + ondevice.callAttr("display_tap", x, y).toString())
                    Thread.sleep(1500)
                    val after = File(filesDir, "frame-tap.png").absolutePath
                    Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", after).toString())
                }

                if (dumpTree) {
                    val id = Regex("display=(\\d+)").find(result)?.groupValues?.get(1)?.toInt() ?: 0
                    val tree = PhoneAgentAccessibilityService.dump(id)
                    File(filesDir, "tree.xml").writeText(tree)
                    Log.i("Pairing", "agent: tree ${tree.length} bytes for display $id")
                }

                if (tapText.isNotEmpty()) {
                    Log.i("Pairing", "agent: " + ondevice.callAttr("display_tap_text", tapText).toString())
                    Thread.sleep(1500)
                    val after = File(filesDir, "frame-text.png").absolutePath
                    Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", after).toString())
                }

                // Stay in the foreground on purpose: the helper that owns the
                // display is a child of this process's adb shell, so letting
                // the service die strands the display with nothing driving it.
                nm.notify(
                    2,
                    Notification.Builder(this, "agent")
                        .setContentTitle(Tr.t("PhoneGo 运行中"))
                        .setContentText(result)
                        .setSmallIcon(android.R.drawable.stat_sys_download)
                        .build()
                )
            } catch (t: Throwable) {
                Log.i("Pairing", "agent failed: ${t.javaClass.simpleName}: ${t.message}")
                started = false
                stopSelf()
            }
        }.start()
        return START_STICKY
    }

    private fun driveExisting(tap: String) {
        Thread {
            try {
                val (x, y) = tap.split(",").map { it.trim().toInt() }
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_tap", x, y).toString())
            } catch (t: Throwable) {
                Log.i("Pairing", "tap failed: ${t.message}")
            }
        }.start()
    }

    private fun clickExisting(label: String) {
        Thread {
            try {
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_tap_text", label).toString())
                Thread.sleep(1200)
                val after = File(filesDir, "frame-text.png").absolutePath
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", after).toString())
            } catch (t: Throwable) {
                Log.i("Pairing", "click failed: ${t.javaClass.simpleName}: ${t.message}")
            }
        }.start()
    }

    private fun typeExisting(value: String) {
        Thread {
            try {
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_input_text", value).toString())
                Thread.sleep(1000)
                val after = File(filesDir, "frame-type.png").absolutePath
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", after).toString())
            } catch (t: Throwable) {
                Log.i("Pairing", "type failed: ${t.javaClass.simpleName}: ${t.message}")
            }
        }.start()
    }

    private fun scrollExisting(direction: String) {
        Thread {
            try {
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                val before = ondevice.callAttr("display_dump").toString()
                Log.i("Pairing", "agent: scroll $direction -> " +
                    ondevice.callAttr("display_scroll", direction).toString())
                Thread.sleep(900)
                val after = ondevice.callAttr("display_dump").toString()
                Log.i("Pairing", "agent: tree ${before.length} -> ${after.length} bytes," +
                    " changed=${before != after}")
                val path = File(filesDir, "frame-scroll.png").absolutePath
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", path).toString())
            } catch (t: Throwable) {
                Log.i("Pairing", "scroll failed: ${t.javaClass.simpleName}: ${t.message}")
            }
        }.start()
    }

    private fun gestureExisting(coords: String) {
        Thread {
            try {
                val (x1, y1, x2, y2) = coords.split(",").map { it.trim().toInt() }
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                val before = ondevice.callAttr("display_dump").toString()
                Log.i("Pairing", "agent: gesture -> " +
                    PhoneAgentAccessibilityService.swipe(x1, y1, x2, y2))
                Thread.sleep(900)
                val after = ondevice.callAttr("display_dump").toString()
                Log.i("Pairing", "agent: virtual tree changed=${before != after}")
                val path = File(filesDir, "frame-gesture.png").absolutePath
                Log.i("Pairing", "agent: " + ondevice.callAttr("display_shot", path).toString())
            } catch (t: Throwable) {
                Log.i("Pairing", "gesture failed: ${t.javaClass.simpleName}: ${t.message}")
            }
        }.start()
    }

    private fun dumpExisting() {
        Thread {
            try {
                val ondevice = Python.getInstance().getModule("phoneagent.ondevice")
                val tree = ondevice.callAttr("display_dump").toString()
                File(filesDir, "tree-ondemand.xml").writeText(tree)
                Log.i("Pairing", "agent: on-demand tree ${tree.length} bytes")
            } catch (t: Throwable) {
                Log.i("Pairing", "dump failed: ${t.message}")
            }
        }.start()
    }


    /** App maps ship as assets; AppMap wants real file paths. */
    private fun extractMaps(): File {
        val dir = File(filesDir, "maps")
        dir.mkdirs()
        // A map removed from the APK must disappear from the device too: the
        // extraction used to only add files, so a map that was retracted kept
        // being loaded long after it stopped shipping.
        dir.listFiles { f -> f.name.endsWith(".yaml") }?.forEach { it.delete() }
        val names = assets.list("maps") ?: emptyArray()
        for (name in names) {
            assets.open("maps/$name").use { input ->
                File(dir, name).outputStream().use { input.copyTo(it) }
            }
        }
        Log.i("Pairing", "agent: maps " + names.joinToString(","))
        return dir
    }

    /** The helper ships as an asset; adbd copies it to /data/local/tmp. */
    private fun extractHelper(): File {
        val out = File(filesDir, "pa-helper.jar")
        assets.open("pa-helper.jar").use { input ->
            out.outputStream().use { input.copyTo(it) }
        }
        return out
    }

    companion object {
        /** How long one acquisition lasts; a live task renews it. */
        private const val WATCH_LOCK_MS = 30 * 60 * 1000L

        private const val SERVER_PORT = 8765
        @Volatile
        private var started = false
        @Volatile
        private var ready = false

        /** Has this process started the agent? The UI asks before healing it. */
        @JvmStatic
        fun isRunning(): Boolean = started

        /**
         * Is the display and the server actually up?
         *
         * "started" is set before any of that work begins, and a health check
         * that runs in that window tears down the display the startup thread is
         * halfway through building -- which is exactly how a failed start looked
         * from the outside.
         */
        @JvmStatic
        fun isReady(): Boolean = ready

        /** One lock for the whole process; renewing it must not stack copies. */
        private var watchLock: android.os.PowerManager.WakeLock? = null

        /**
         * Keep the CPU awake while a task is being watched.
         *
         * The lock used to be taken once for thirty minutes when the agent
         * started. A cross-app task can run longer than that, and the device
         * then slept in the middle of it. The activity renews it on every poll
         * that comes back, so it lasts exactly as long as someone is watching.
         */
        @JvmStatic
        fun renewWakeLock(context: android.content.Context) {
            try {
                if (watchLock == null) {
                    val power = context.getSystemService(android.content.Context.POWER_SERVICE)
                            as? android.os.PowerManager ?: return
                    watchLock = power.newWakeLock(
                        android.os.PowerManager.PARTIAL_WAKE_LOCK, "PhoneGo::watch"
                    ).also { it.setReferenceCounted(false) }
                }
                val lock = watchLock ?: return
                if (lock.isHeld) {
                    lock.release()
                }
                lock.acquire(WATCH_LOCK_MS)
            } catch (t: Throwable) {
                android.util.Log.i("Pairing", "wake lock renew failed: ${t.message}")
            }
        }

        /**
         * Reload the agent after the configuration changed.
         *
         * The Python side keeps the display and the server in module globals,
         * so they have to be released first or the restart would reuse the old
         * key and the old display.
         */
        @JvmStatic
        fun restart(context: android.content.Context) {
            // ready is what the app consults before restarting a silent agent.
            // It is only assigned when start_agent answers "listening", so a
            // server that hung after that kept the flag set and the app never
            // tried again. Clear it here: the agent is about to be rebuilt.
            ready = false
            Thread {
                runCatching {
                    Python.getInstance().getModule("phoneagent.ondevice")
                        .callAttr("shutdown")
                }.onFailure { Log.i("Pairing", "shutdown failed: ${it.message}") }
                started = false
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    context.startForegroundService(Intent(context, AgentService::class.java))
                }
            }.start()
        }
    }
}
