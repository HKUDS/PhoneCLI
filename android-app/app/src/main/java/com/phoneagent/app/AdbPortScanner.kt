package com.phoneagent.app

import android.content.Context
import android.util.Log
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/**
 * Finds the adbd port by asking it a question instead of trusting a record.
 *
 * mDNS advertises `_adb-tls-connect._tcp`, but the record can describe a port
 * from an earlier adbd (observed here: it said 45341 while 44063 was the one
 * actually listening). App uids cannot read `/proc/net/tcp6` and
 * `service.adb.tls.port` is restricted, so the only dependable answer comes
 * from probing: adbd is the one service on loopback that answers a plain CNXN
 * with STLS.
 *
 * Closed ports on loopback refuse immediately, so walking the ephemeral range
 * is cheap -- a few hundred milliseconds with a bounded worker pool.
 */
object AdbPortScanner {

    private const val TAG = "Pairing"

    /** Linux default ephemeral range; adbd's port comes from here. */
    private val RANGE = 32768..60999

    fun scan(workers: Int = 192, connectTimeoutMs: Int = 40): Int? {
        val next = AtomicInteger(RANGE.first)
        val found = AtomicReference<Int?>(null)
        val threads = (1..workers).map {
            thread(isDaemon = true, name = "pa-port-$it") {
                while (found.get() == null) {
                    val port = next.getAndIncrement()
                    if (port > RANGE.last) return@thread
                    if (accepts(port, connectTimeoutMs) && MdnsAdbFinder.speaksAdb(port)) {
                        found.compareAndSet(null, port)
                        Log.i(TAG, "adbd answered on $port")
                        return@thread
                    }
                }
            }
        }
        threads.forEach { it.join() }
        return found.get()
    }

    private fun accepts(port: Int, timeoutMs: Int): Boolean = try {
        Socket().use { socket ->
            socket.connect(InetSocketAddress("127.0.0.1", port), timeoutMs)
            true
        }
    } catch (t: Throwable) {
        false
    }

    /**
     * Port for the app to dial.
     *
     * Order matters: the last port that worked is tried first (restarts are
     * common and a scan is not free), then mDNS, then the scan. Every answer is
     * confirmed by the STLS probe and the winner is remembered.
     */
    fun discover(context: Context): Int? {
        val prefs = context.getSharedPreferences("agent", Context.MODE_PRIVATE)
        val cached = prefs.getInt("adbd_port", 0)
        if (cached > 0 && MdnsAdbFinder.speaksAdb(cached)) {
            Log.i(TAG, "using the remembered port $cached")
            return cached
        }

        val advertised = MdnsAdbFinder.findPort(context)
        if (advertised != null) {
            if (MdnsAdbFinder.speaksAdb(advertised)) {
                prefs.edit().putInt("adbd_port", advertised).apply()
                Log.i(TAG, "using the advertised port $advertised")
                return advertised
            }
            Log.i(TAG, "mDNS said $advertised but it is stale; scanning")
        }

        // a scan can lose a race with adbd tearing down the previous session,
        // so give it a second chance before giving up
        repeat(2) { attempt ->
            val found = scan()
            if (found != null) {
                prefs.edit().putInt("adbd_port", found).apply()
                return found
            }
            Log.i(TAG, "scan ${attempt + 1} found nothing")
        }
        return null
    }
}
