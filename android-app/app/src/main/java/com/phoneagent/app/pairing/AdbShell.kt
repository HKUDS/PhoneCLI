package com.phoneagent.app.pairing

import android.annotation.SuppressLint
import android.util.Base64
import java.io.DataInputStream
import java.io.EOFException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyFactory
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Principal
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.X509TrustManager

/**
 * ADB transport over the TLS channel the pairing established.
 *
 * Python's ssl (OpenSSL) will not send a self-signed client certificate when
 * the server's CertificateRequest lists CAs it does not match, so adbd closes
 * the connection. Conscrypt sends it unconditionally, which is why this lives
 * in Kotlin next to the pairing code.
 */
object AdbShell {

    private const val A_VERSION = 0x01000001
    private const val A_STLS_VERSION = 0x01000000
    private const val A_MAXDATA = 1048576   // what the real client advertises
    private const val CNXN = 0x4E584E43
    private const val AUTH = 0x48545541
    private const val OPEN = 0x4E45504F
    private const val OKAY = 0x59414B4F
    private const val CLSE = 0x45534C43
    private const val WRTE = 0x45545257
    private const val STLS = 0x534c5453   // "STLS": adbd asks to upgrade to TLS

    /** What the real client advertises; adbd echoes the intersection back. */
    private const val HOST_FEATURES = "shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex," +
        "abb,abb_exec,remount_shell,track_app,sendrecv_v2,deviceside_enx," +
        "sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,openscreen_mdns"

    private fun pemToDer(pem: ByteArray, label: String): ByteArray {
        val text = String(pem)
        val body = text.substringAfter("-----BEGIN $label-----").substringBefore("-----END $label-----")
        return Base64.decode(body.replace(Regex("\\s"), ""), Base64.DEFAULT)
    }

    private fun identity(certPem: ByteArray, keyPem: ByteArray): Pair<X509Certificate, PrivateKey> {
        val cert = CertificateFactory.getInstance("X.509")
            .generateCertificate(pemToDer(certPem, "CERTIFICATE").inputStream()) as X509Certificate
        val key = KeyFactory.getInstance("RSA")
            .generatePrivate(PKCS8EncodedKeySpec(pemToDer(keyPem, "PRIVATE KEY")))
        return cert to key
    }

    @SuppressLint("TrustAllX509TrustManager", "CustomX509TrustManager")
    private fun context(cert: X509Certificate, key: PrivateKey): SSLContext {
        val km = object : X509ExtendedKeyManager() {
            override fun getCertificateChain(alias: String?): Array<X509Certificate> {
                android.util.Log.i("Pairing", "adb keymanager: chain for $alias")
                return arrayOf(cert)
            }
            override fun getPrivateKey(alias: String?): PrivateKey = key
            override fun getClientAliases(keyType: String?, issuers: Array<Principal>?): Array<String>? =
                if (keyType == null || keyType.equals("RSA", true)) arrayOf("pa") else null
            override fun chooseClientAlias(keyType: Array<String>?, issuers: Array<Principal>?, socket: Socket?): String? {
                android.util.Log.i("Pairing", "adb keymanager: chooseClientAlias ${keyType?.joinToString(",")}")
                return if (keyType == null || keyType.any { it.equals("RSA", true) }) "pa" else null
            }
            override fun getServerAliases(keyType: String?, issuers: Array<Principal>?): Array<String>? = null
            override fun chooseServerAlias(keyType: String?, issuers: Array<Principal>?, socket: Socket?): String? = null
        }
        // Trust all, deliberately: the peer is adbd on this same phone and its
        // certificate is generated per pairing, so there is no CA to check it
        // against. What actually authenticates the exchange is SPAKE2 with the
        // six-digit code from Settings -- the TLS layer here only carries it.
        val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        })
        return SSLContext.getInstance("TLS").apply { init(arrayOf<KeyManager>(km), trustAll, null) }
    }

    private fun frame(command: Int, arg0: Int, arg1: Int, data: ByteArray = ByteArray(0)): ByteArray {
        val crc = java.util.zip.CRC32().apply { update(data) }.value.toInt()
        return ByteBuffer.allocate(24 + data.size).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(command).putInt(arg0).putInt(arg1).putInt(data.size).putInt(crc)
            .putInt(command.inv()).put(data).array()
    }

    class Frame(val command: Int, val arg0: Int, val arg1: Int, val data: ByteArray)

    private fun read(input: DataInputStream): Frame {
        val header = ByteArray(24)
        input.readFully(header)
        val bb = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
        val command = bb.int; val arg0 = bb.int; val arg1 = bb.int
        val len = bb.int; bb.int; val magic = bb.int
        if (command.inv() != magic) throw IllegalStateException("bad magic")
        val data = ByteArray(len)
        if (len > 0) {
            try {
                input.readFully(data)
            } catch (e: EOFException) {
                throw IllegalStateException("connection closed mid-frame", e)
            }
        }
        return Frame(command, arg0, arg1, data)
    }

    /** Runs one command and returns its output together with the device banner. */
    fun shell(certPem: ByteArray, keyPem: ByteArray, host: String, port: Int, command: String, timeoutMs: Int = 15000): String {
        val (cert, key) = identity(certPem, keyPem)
        // build the context up front: adbd appears to time out quickly if the
        // ClientHello is late after STLS
        val tlsContext = context(cert, key)
        val raw = Socket()
        raw.tcpNoDelay = true
        raw.connect(InetSocketAddress(host, port), timeoutMs)
        raw.soTimeout = timeoutMs
        // adbd speaks plain first: it answers our CNXN with STLS and only
        // then expects the TLS handshake on the same socket
        val plainIn = DataInputStream(raw.getInputStream())
        // the real client opens with CNXN on the plain socket; the device then
        // answers STLS, and the client acks with STLS before upgrading
        raw.getOutputStream().write(frame(CNXN, A_VERSION, A_MAXDATA, ("host::features=$HOST_FEATURES" + "\u0000").toByteArray()))
        raw.getOutputStream().flush()
        val hello = read(plainIn)
        if (hello.command == CNXN) {
            // the device accepted us in plain ADB: no upgrade needed
            android.util.Log.i("Pairing", "plain CNXN accepted: " + String(hello.data).trimEnd('\u0000'))
            return runService(DataInputStream(raw.getInputStream()), raw.getOutputStream(), command, String(hello.data).trimEnd('\u0000'))
        }
        if (hello.command == STLS) {
            // acknowledge: the real client answers STLS with STLS and only then
            // upgrades, which is what the device waits for
            raw.getOutputStream().write(frame(STLS, A_STLS_VERSION, 0))
            raw.getOutputStream().flush()
            android.util.Log.i("Pairing", "STLS acked, upgrading to TLS")
        } else if (hello.command != CNXN) {
            throw IllegalStateException("expected STLS, got 0x%08x".format(hello.command))
        }
        // a read timeout on the socket breaks the TLS handshake, which expects
        // blocking reads; restore it only afterwards
        raw.soTimeout = 0
        val ssl = tlsContext.socketFactory.createSocket(raw, host, port, true) as SSLSocket
        ssl.soTimeout = 0
        ssl.useClientMode = true
        // adbd's TLS is 1.3-only; Conscrypt may otherwise settle on 1.2
        runCatching { ssl.enabledProtocols = arrayOf("TLSv1.3") }
        // adbd negotiates the protocol with ALPN; without "adb" it drops the
        // connection during the handshake
        ssl.sslParameters = ssl.sslParameters.apply { applicationProtocols = arrayOf("adb") }
        ssl.startHandshake()
        android.util.Log.i("Pairing", "adb TLS ${ssl.session.protocol} alpn=${ssl.applicationProtocol}")

        ssl.soTimeout = timeoutMs
        val output: OutputStream = ssl.outputStream
        val input = DataInputStream(ssl.inputStream)

        // The CNXN went out in the clear before the upgrade and adbd already
        // has it; after the handshake the *device* speaks first. Sending a
        // second CNXN here makes adbd answer with another STLS -- it treats
        // any CNXN as "the peer wants to start TLS" -- and the connection is
        // then stuck, because the channel is already inside TLS.
        val first = try {
            read(input)
        } catch (t: Throwable) {
            android.util.Log.i("Pairing", "no CNXN banner: ${t.javaClass.simpleName}: ${t.message}")
            throw t
        }
        android.util.Log.i("Pairing", "device banner cmd=0x%08x len=%d".format(first.command, first.data.size))
        if (first.command == AUTH) throw IllegalStateException("device asked for AUTH: key not paired")
        if (first.command == STLS) throw IllegalStateException("device asked to upgrade again: the plain CNXN did not reach it")
        if (first.command != CNXN) throw IllegalStateException("expected CNXN, got 0x%08x".format(first.command))
        val banner = String(first.data).trimEnd('\u0000')
        return runService(input, output, command, banner)
    }

    /**
     * Opens the shell service and collects its output.
     *
     * `shell,v2,raw:` makes adbd multiplex stdout/stderr/exit-code into small
     * packets inside each WRTE instead of handing us one undifferentiated
     * byte stream, so we learn the exit status of the command too.
     */
    private fun runService(input: DataInputStream, output: OutputStream, command: String, banner: String): String {
        val localId = 1
        var remoteId = 0
        output.write(frame(OPEN, localId, 0, "shell,v2,raw:$command\u0000".toByteArray()))
        output.flush()

        val stdout = StringBuilder()
        val stderr = StringBuilder()
        var exitCode = -1
        var frames = 0
        while (true) {
            val f = read(input)
            frames++
            if (frames <= 6) {
                android.util.Log.i("Pairing", "frame 0x%08x a0=%d a1=%d len=%d".format(f.command, f.arg0, f.arg1, f.data.size))
            }
            when (f.command) {
                OKAY -> remoteId = f.arg0
                WRTE -> {
                    if (f.arg1 != 0) remoteId = f.arg1
                    var at = 0
                    while (at + 5 <= f.data.size) {
                        val id = f.data[at].toInt() and 0xFF
                        val len = (f.data[at + 1].toInt() and 0xFF) or
                            ((f.data[at + 2].toInt() and 0xFF) shl 8) or
                            ((f.data[at + 3].toInt() and 0xFF) shl 16) or
                            ((f.data[at + 4].toInt() and 0xFF) shl 24)
                        if (at + 5 + len > f.data.size) break
                        val chunk = String(f.data, at + 5, len)
                        when (id) {
                            1 -> stdout.append(chunk)
                            2 -> stderr.append(chunk)
                            3 -> if (len > 0) exitCode = f.data[at + 5].toInt() and 0xFF
                        }
                        at += 5 + len
                    }
                    output.write(frame(OKAY, localId, remoteId))
                    output.flush()
                }
                CLSE -> return finish(stdout, stderr, exitCode, banner)
                STLS -> throw IllegalStateException("device asked to upgrade again mid-service")
                else -> throw IllegalStateException("unexpected frame 0x%08x".format(f.command))
            }
        }
    }

    private fun finish(stdout: StringBuilder, stderr: StringBuilder, exitCode: Int, banner: String): String {
        val text = stdout.toString().trimEnd()
        if (stderr.isNotBlank()) {
            android.util.Log.i("Pairing", "stderr: " + stderr.toString().trimEnd())
        }
        android.util.Log.i("Pairing", "shell exit=$exitCode bytes=${text.length}")
        return text + "\n--\nexit=" + exitCode + "\n" + banner
    }
}
