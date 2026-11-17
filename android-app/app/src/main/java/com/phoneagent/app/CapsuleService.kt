package com.phoneagent.app

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.PixelFormat
import android.os.IBinder
import android.provider.Settings
import android.util.Base64
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * A small picture of the agent's screen that stays visible while you use the
 * phone for something else.
 *
 * The card in the chat only helps when the app is open; the point of a floating
 * capsule is to see progress from wherever you are, and to get back with one
 * tap. It is fed by /frame, which replays the last picture the task published
 * rather than touching the device -- the adb transport is single-channel.
 */
class CapsuleService : Service() {

    private val tag = "Pairing"
    private var windowManager: WindowManager? = null
    private var root: View? = null
    private var thumb: ImageView? = null
    private var dot: TextView? = null
    private val alive = AtomicBoolean(true)

    companion object {
        const val ACTION_CANCEL = "com.phoneagent.app.CANCEL_TASK"
        const val ACTION_OPEN = "com.phoneagent.app.OPEN_APP"
        private const val NOTIFICATION_ID = 3
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // The floating window is retired: the mirror is a view inside the app
        // now (see activity_main.xml). This service is kept only so that a stale
        // START_STICKY restart from an older installation cannot bring a system
        // overlay back; it answers, does nothing, and goes away.
        stopSelf()
        return START_NOT_STICKY
    }

    private fun onStartCommandLegacy(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CANCEL -> {
                Thread { cancelRunningTask() }.start()
                toast(Tr.t("正在取消任务"))
            }
            ACTION_OPEN -> openApp()
        }
        return START_STICKY
    }

    override fun onCreate() {
        super.onCreate()
        if (!Settings.canDrawOverlays(this)) {
            Log.w(tag, "capsule: no overlay permission")
            stopSelf()
            return
        }
        startForegroundWithNotification()
        show()
        Thread { poll() }.start()
    }

    private fun startForegroundWithNotification() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel("capsule", "Floating capsule", NotificationManager.IMPORTANCE_LOW)
        )
        startForeground(NOTIFICATION_ID, buildNotification(Tr.t("悬浮窗显示中")))
    }

    /**
     * The reliable way back into the app.
     *
     * MIUI blocks an app from starting an activity out of the background, so the
     * capsule's own tap can fail silently (measured). A notification's
     * PendingIntent is allowed to do it, which is why the same action lives
     * here as well -- and why a failed tap posts a heads-up instead of doing
     * nothing.
     */
    private fun buildNotification(text: String): android.app.Notification {
        val open = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val openPending = android.app.PendingIntent.getActivity(
            this, 0, open,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT
        )
        val cancel = Intent(this, CapsuleService::class.java).setAction(ACTION_CANCEL)
        val cancelPending = android.app.PendingIntent.getService(
            this, 1, cancel,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT
        )
        return android.app.Notification.Builder(this, "capsule")
            .setContentTitle("PhoneGo")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentIntent(openPending)
            .setOngoing(true)
            .addAction(android.app.Notification.Action.Builder(
                null, Tr.t("停止任务"), cancelPending
            ).build())
            .build()
    }

    private fun toast(text: String) {
        android.os.Handler(mainLooper).post {
            android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    private fun openApp() {
        val open = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(open)
        } catch (t: Throwable) {
            Log.w(tag, "capsule: could not open the app ($t)")
        }
    }

    /** A heads-up so a blocked tap still leads somewhere. */
    private fun askToOpen() {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel("capsule_open") == null) {
            manager.createNotificationChannel(
                NotificationChannel(
                    "capsule_open", Tr.t("打开 PhoneAgent"), NotificationManager.IMPORTANCE_HIGH
                )
            )
        }
        val open = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pending = android.app.PendingIntent.getActivity(
            this, 2, open,
            android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT
        )
        manager.notify(4, android.app.Notification.Builder(this, "capsule_open")
            .setContentTitle(Tr.t("点这里回到 PhoneAgent"))
            .setContentText(Tr.t("系统不允许悬浮窗直接打开 App"))
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentIntent(pending)
            .setAutoCancel(true)
            .build())
        toast(Tr.t("点通知栏回到 PhoneAgent"))
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun show() {
        val density = resources.displayMetrics.density
        val width = (84 * density).toInt()
        val height = (168 * density).toInt()

        val box = FrameLayout(this)
        val image = ImageView(this)
        image.layoutParams = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT
        )
        image.scaleType = ImageView.ScaleType.FIT_CENTER
        image.setBackgroundColor(0xCC111111.toInt())
        image.setPadding(2, 2, 2, 2)
        box.addView(image)

        val status = TextView(this)
        status.text = getString(R.string.dot_filled)
        status.setTextColor(0xFF44CC88.toInt())
        status.textSize = 12f
        status.setPadding(6, 2, 6, 2)
        val statusParams = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT
        )
        statusParams.gravity = Gravity.TOP or Gravity.START
        box.addView(status, statusParams)

        thumb = image
        dot = status

        val params = WindowManager.LayoutParams(
            width, height,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        )
        params.gravity = Gravity.TOP or Gravity.END
        params.x = (8 * density).toInt()
        params.y = (160 * density).toInt()

        // tap brings the app back; drag moves the capsule; long-press stops
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var moved = false
        var downAt = 0L

        box.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX; downY = event.rawY
                    startX = params.x; startY = params.y
                    moved = false
                    downAt = System.currentTimeMillis()
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (downX - event.rawX).toInt()
                    val dy = (event.rawY - downY).toInt()
                    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) moved = true
                    if (moved) {
                        params.x = startX + dx
                        params.y = startY + dy
                        windowManager?.updateViewLayout(box, params)
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    val held = System.currentTimeMillis() - downAt
                    if (!moved && held > 600) {
                        // Off the main thread: this does HTTP, and a network
                        // call on the UI thread throws immediately (which is
                        // why long-press appeared to do nothing).
                        Thread { cancelRunningTask() }.start()
                    } else if (!moved) {
                        openApp()
                        // MIUI refuses a background activity start; the
                        // notification is the way that always works.
                        askToOpen()
                    }
                    true
                }
                else -> false
            }
        }

        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        windowManager?.addView(box, params)
        root = box
    }

    private fun poll() {
        while (alive.get()) {
            try {
                val busy = isTaskRunning()
                val png = getBytes("/frame")
                if (png != null && png.isNotEmpty()) {
                    val bitmap: Bitmap? = BitmapFactory.decodeByteArray(png, 0, png.size)
                    if (bitmap != null) {
                        thumb?.post { thumb?.setImageBitmap(bitmap) }
                    }
                }
                dot?.post {
                    dot?.setText(
                        getString(if (busy) R.string.dot_filled else R.string.dot_hollow)
                    )
                    dot?.setTextColor(if (busy) 0xFF44CC88.toInt() else 0xFF888888.toInt())
                }
            } catch (t: Throwable) {
                // the agent may be between tasks; keep the last picture
            }
            try {
                Thread.sleep(1200)
            } catch (e: InterruptedException) {
                break
            }
        }
    }

    /** /frame serves PNG bytes; base64 is only used inside the task JSON. */
    private fun getBytes(path: String): ByteArray? {
        return try {
            val conn = URL("http://127.0.0.1:8765$path").openConnection() as HttpURLConnection
            conn.connectTimeout = 2000
            conn.readTimeout = 4000
            if (conn.responseCode != 200) {
                return null
            }
            conn.inputStream.use { it.readBytes() }
        } catch (t: Throwable) {
            null
        }
    }

    private fun get(path: String): String {
        val conn = URL("http://127.0.0.1:8765$path").openConnection() as HttpURLConnection
        conn.connectTimeout = 2000
        conn.readTimeout = 4000
        if (path == "/frame") {
            val out = java.io.ByteArrayOutputStream()
            conn.inputStream.use { it.copyTo(out) }
            return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        }
        return conn.inputStream.bufferedReader().readText()
    }

    /** True while any task is running, queued or waiting for an answer. */
    private fun isTaskRunning(): Boolean {
        return try {
            val tasks = JSONObject(get("/tasks")).optJSONObject("tasks") ?: return false
            tasks.keys().asSequence().any { key ->
                val state = tasks.optJSONObject(key)?.optString("state")
                state == "running" || state == "queued"
            }
        } catch (t: Throwable) {
            false
        }
    }

    private fun cancelRunningTask() {
        try {
            val tasks = JSONObject(get("/tasks")).optJSONObject("tasks") ?: return
            for (id in tasks.keys()) {
                if (tasks.optJSONObject(id)?.optString("state") == "running") {
                    val conn = URL("http://127.0.0.1:8765/task/$id/cancel")
                        .openConnection() as HttpURLConnection
                    conn.requestMethod = "POST"
                    conn.connectTimeout = 2000
                    conn.readTimeout = 4000
                    conn.outputStream.use { it.write("{}".toByteArray()) }
                    conn.inputStream.close()
                }
            }
        } catch (t: Throwable) {
            Log.w(tag, "capsule: cancel failed $t")
        }
    }

    override fun onDestroy() {
        alive.set(false)
        root?.let { runCatching { windowManager?.removeView(it) } }
        root = null
        super.onDestroy()
    }
}
