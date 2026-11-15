package com.phoneagent.app

import android.content.Context
import android.provider.Settings

/**
 * Device facts the Python side cannot read by itself.
 *
 * When the adb connection dies mid-task the agent rebuilds it, and if that
 * fails the honest reason matters: "reopen the app" is the wrong advice when
 * wireless debugging has simply been switched off, which is what MIUI does on
 * reboot and after long idle periods.
 */
object AgentHealth {
    /**
     * Seconds since the interpreter last wrote its heartbeat, or -1 with no file.
     *
     * A frozen interpreter answers nothing -- not even healthy(), so the check has
     * to live where the freeze cannot reach it: the heartbeat file is written by a
     * Python thread every thirty seconds, and reading its age is a file stat.
     */
    @JvmStatic
    fun beatSeconds(context: Context): Long {
        val file = java.io.File(context.cacheDir, "chaquopy/tmp/phoneagent-beat.txt")
        if (!file.exists()) return -1
        val age = (System.currentTimeMillis() - file.lastModified()) / 1000
        return if (age < 0) 0 else age
    }

    /** adb_wifi_enabled, i.e. 设置 → 开发者选项 → 无线调试. */
    @JvmStatic
    fun wirelessDebugging(context: Context): Boolean {
        return try {
            Settings.Global.getInt(
                context.contentResolver, "adb_wifi_enabled", 0,
            ) == 1
        } catch (t: Throwable) {
            false
        }
    }
}
