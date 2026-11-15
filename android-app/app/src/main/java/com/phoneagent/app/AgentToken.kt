package com.phoneagent.app

import android.content.Context
import java.io.File
import java.security.SecureRandom

/**
 * The shared secret between the app and the agent it runs.
 *
 * The agent listens on 127.0.0.1, and on Android every installed app can reach
 * loopback -- so without a token any app on the phone could drive the agent or
 * read the screen through /dump. Both sides read this one file, so a restart of
 * either still agrees on the value: the service announces it before starting
 * Python, and the UI sends it on every request.
 */
object AgentToken {
    private const val FILE = "agent-token"

    fun ensure(context: Context): String {
        val file = File(context.filesDir, FILE)
        val existing = runCatching { file.readText().trim() }.getOrNull()
        if (!existing.isNullOrEmpty()) return existing
        val random = SecureRandom()
        val token = (1..32)
            .map { "0123456789abcdef"[random.nextInt(16)] }
            .joinToString("")
        runCatching {
            file.writeText(token)
            file.setReadable(false, false)   // owner only
            file.setReadable(true, true)
            file.setWritable(false, false)
            file.setWritable(true, true)
        }
        return token
    }
}
