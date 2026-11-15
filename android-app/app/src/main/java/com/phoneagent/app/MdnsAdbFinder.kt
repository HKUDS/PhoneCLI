package com.phoneagent.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Finds the port adbd listens on for wireless debugging.
 *
 * The app cannot read `/proc/net/tcp6` (the file is not accessible to app
 * uids) and `service.adb.tls.port` is only visible to adbd itself, so mDNS is
 * the one channel that works: the system advertises
 * `_adb-tls-connect._tcp` and the resolved record carries the port.
 */
object MdnsAdbFinder {

    private const val SERVICE = "_adb-tls-connect._tcp"
    private const val TAG = "Pairing"

    /** Returns the adbd port, or null if nothing answered within [timeoutMs]. */
    fun findPort(context: Context, timeoutMs: Long = 6000): Int? {
        val nsd = context.getSystemService(Context.NSD_SERVICE) as? NsdManager ?: return null
        val latch = CountDownLatch(1)
        var port = 0
        var discovery: NsdManager.DiscoveryListener? = null
        // resolveService rejects a listener that is already resolving (the
        // record is advertised on several interfaces)
        val resolving = java.util.concurrent.atomic.AtomicBoolean(false)

        val resolve = object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                Log.i(TAG, "mdns resolve failed: $errorCode")
                latch.countDown()
            }

            override fun onServiceResolved(info: NsdServiceInfo) {
                Log.i(TAG, "mdns resolved ${info.serviceName} -> ${info.host}:${info.port}")
                port = info.port
                latch.countDown()
            }
        }

        discovery = object : NsdManager.DiscoveryListener {
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.i(TAG, "mdns discovery start failed: $errorCode")
                latch.countDown()
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onDiscoveryStarted(serviceType: String) {
                Log.i(TAG, "mdns browsing $serviceType")
            }

            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onServiceLost(info: NsdServiceInfo) {}

            override fun onServiceFound(info: NsdServiceInfo) {
                if (!info.serviceType.startsWith(SERVICE)) return
                if (!resolving.compareAndSet(false, true)) return
                runCatching { nsd.resolveService(info, resolve) }
                    .onFailure { Log.i(TAG, "resolve threw: $it") }
            }
        }

        return try {
            nsd.discoverServices(SERVICE, NsdManager.PROTOCOL_DNS_SD, discovery)
            latch.await(timeoutMs, TimeUnit.MILLISECONDS)
            port.takeIf { it > 0 }
        } catch (t: Throwable) {
            Log.i(TAG, "mdns discovery failed: ${t.javaClass.simpleName}: ${t.message}")
            null
        } finally {
            runCatching { discovery?.let { nsd.stopServiceDiscovery(it) } }
        }
    }

    /**
     * Port plus a sanity check.
     *
     * The record can outlive a toggle-off, so a port is only accepted once
     * something actually answers the ADB pre-TLS greeting on it.
     */
    fun findLivePort(context: Context, timeoutMs: Long = 6000): Int? {
        val port = findPort(context, timeoutMs) ?: return null
        return if (speaksAdb(port)) port else null
    }

    /** True if the TCP endpoint answers our plain CNXN with STLS. */
    fun speaksAdb(port: Int): Boolean {
        return try {
            java.net.Socket("127.0.0.1", port).use { socket ->
                socket.soTimeout = 3000
                val frame = java.nio.ByteBuffer.allocate(24)
                    .order(java.nio.ByteOrder.LITTLE_ENDIAN)
                    .putInt(0x4E584E43).putInt(0x01000001).putInt(1 shl 20)
                    .putInt(0).putInt(0).putInt(0x4E584E43.inv())
                    .array()
                socket.getOutputStream().write(frame)
                socket.getOutputStream().flush()
                val header = ByteArray(24)
                var read = 0
                while (read < 24) {
                    val n = socket.getInputStream().read(header, read, 24 - read)
                    if (n <= 0) return false
                    read += n
                }
                val command = java.nio.ByteBuffer.wrap(header)
                    .order(java.nio.ByteOrder.LITTLE_ENDIAN).int
                command == 0x534C5453 // STLS
            }
        } catch (t: Throwable) {
            false
        }
    }
}
