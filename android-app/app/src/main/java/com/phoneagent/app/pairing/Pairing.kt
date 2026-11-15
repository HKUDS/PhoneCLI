package com.phoneagent.app.pairing

import android.annotation.SuppressLint
import android.util.Base64
import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.net.InetSocketAddress
import java.net.Socket
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Principal
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509ExtendedKeyManager
import javax.security.auth.x500.X500Principal

/**
 * ADB wireless-debugging pairing, ported from AOSP's pairing_connection.cpp,
 * pairing_auth/aes_128_gcm.cpp and crypto/curve25519/spake25519.cc.
 *
 * The one part that cannot be done in Python: the pairing password is the code
 * plus 64 bytes of TLS exported keying material (label "adb-label"), and
 * Python's ssl module does not expose RFC 5705 exporters.
 */
object Pairing {

    data class Result(val ok: Boolean, val detail: String)

    /** Minimal DER writer for the self-signed certificate. */
    private object Der {
        fun tlv(tag: Int, body: ByteArray): ByteArray {
            val out = ByteArrayOutputStream()
            out.write(tag)
            val n = body.size
            when {
                n < 0x80 -> out.write(n)
                n < 0x100 -> { out.write(0x81); out.write(n) }
                else -> { out.write(0x82); out.write(n shr 8); out.write(n and 0xff) }
            }
            out.write(body)
            return out.toByteArray()
        }
        fun seq(vararg parts: ByteArray) = tlv(0x30, parts.reduce { a, b -> a + b })
        fun set(vararg parts: ByteArray) = tlv(0x31, parts.reduce { a, b -> a + b })
        fun integer(v: BigInteger): ByteArray {
            var b = v.toByteArray()
            if (b[0].toInt() and 0x80 != 0) b = byteArrayOf(0) + b
            return tlv(0x02, b)
        }
        fun oid(dotted: String): ByteArray {
            val parts = dotted.split(".").map { it.toLong() }
            val out = ByteArrayOutputStream()
            out.write((40 * parts[0] + parts[1]).toInt())
            for (p in parts.drop(2)) {
                if (p < 128) out.write(p.toInt())
                else {
                    val tmp = mutableListOf<Int>()
                    var v = p
                    while (v > 0) { tmp.add((v and 0x7f).toInt()); v = v shr 7 }
                    tmp.reverse()
                    for ((i, byte) in tmp.withIndex()) out.write(byte or if (i == tmp.size - 1) 0 else 0x80)
                }
            }
            return tlv(0x06, out.toByteArray())
        }
        fun utf8(s: String) = tlv(0x0c, s.toByteArray())
        fun bitString(bytes: ByteArray) = tlv(0x03, byteArrayOf(0) + bytes)
        fun utcTime(date: Date): ByteArray {
            val fmt = SimpleDateFormat("yyMMddHHmmss'Z'", Locale.US)
            fmt.timeZone = TimeZone.getTimeZone("UTC")
            return tlv(0x17, fmt.format(date).toByteArray())
        }
    }

    /** A self-signed certificate for [keys]; the device validates only the chain shape. */
    private fun selfSigned(keys: KeyPair): X509Certificate {
        val name = X500Principal("CN=phoneagent")
        val nameDer = name.encoded
        val sha256Rsa = Der.seq(Der.oid("1.2.840.113549.1.1.11"), Der.tlv(0x05, ByteArray(0)))
        val spki = keys.public.encoded  // already a SubjectPublicKeyInfo for RSA
        val serial = BigInteger(64, SecureRandom()).abs().add(BigInteger.ONE)
        val now = System.currentTimeMillis()
        val tbs = Der.seq(
            Der.tlv(0xa0, Der.integer(BigInteger.valueOf(2))),
            Der.integer(serial),
            sha256Rsa,
            nameDer,
            Der.seq(Der.utcTime(Date(now - 86_400_000L)), Der.utcTime(Date(now + 3650L * 86_400_000L))),
            nameDer,
            spki
        )
        val sig = java.security.Signature.getInstance("SHA256withRSA").run {
            initSign(keys.private as PrivateKey); update(tbs); sign()
        }
        val certDer = Der.seq(tbs, sha256Rsa, Der.bitString(sig))
        val factory = java.security.cert.CertificateFactory.getInstance("X.509")
        return factory.generateCertificate(certDer.inputStream()) as X509Certificate
    }

    /** The stored certificate if present, otherwise a freshly signed one. */
    private fun loadOrCreateCert(keys: KeyPair, storeDir: String?): X509Certificate {
        if (storeDir != null) {
            val certFile = java.io.File(java.io.File(storeDir), "pair-cert.pem")
            if (certFile.exists()) {
                try {
                    val der = Base64.decode(
                        String(certFile.readBytes()).substringAfter("-----BEGIN CERTIFICATE-----")
                            .substringBefore("-----END CERTIFICATE-----")
                            .replace(Regex("\\s"), ""),
                        Base64.DEFAULT,
                    )
                    val stored = java.security.cert.CertificateFactory.getInstance("X.509")
                        .generateCertificate(der.inputStream()) as X509Certificate
                    if (stored.publicKey == keys.public) {
                        android.util.Log.i("Pairing", "reusing the stored certificate")
                        return stored
                    }
                } catch (t: Throwable) {
                    android.util.Log.i("Pairing", "stored certificate unreadable: ${t.message}")
                }
            }
        }
        return selfSigned(keys)
    }

    /** The identity from a previous run, or a fresh one that is stored right away. */
    private fun loadOrCreateIdentity(storeDir: String?): KeyPair {
        if (storeDir != null) {
            val dir = java.io.File(storeDir)
            val keyFile = java.io.File(dir, "pair-key.pem")
            val certFile = java.io.File(dir, "pair-cert.pem")
            if (keyFile.exists() && certFile.exists()) {
                try {
                    val der = Base64.decode(
                        String(keyFile.readBytes()).substringAfter("-----BEGIN PRIVATE KEY-----")
                            .substringBefore("-----END PRIVATE KEY-----")
                            .replace(Regex("\\s"), ""),
                        Base64.DEFAULT,
                    )
                    val key = java.security.KeyFactory.getInstance("RSA")
                        .generatePrivate(java.security.spec.PKCS8EncodedKeySpec(der))
                    val certDer = Base64.decode(
                        String(certFile.readBytes()).substringAfter("-----BEGIN CERTIFICATE-----")
                            .substringBefore("-----END CERTIFICATE-----")
                            .replace(Regex("\\s"), ""),
                        Base64.DEFAULT,
                    )
                    val cert = java.security.cert.CertificateFactory.getInstance("X.509")
                        .generateCertificate(certDer.inputStream()) as X509Certificate
                    android.util.Log.i("Pairing", "reusing the stored identity")
                    return KeyPair(cert.publicKey, key)
                } catch (t: Throwable) {
                    android.util.Log.i("Pairing", "stored identity unreadable: ${t.message}")
                }
            }
        }
        return KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
    }

    @SuppressLint("TrustAllX509TrustManager", "CustomX509TrustManager")

    private fun context(keys: KeyPair, cert: X509Certificate): SSLContext {
        val km = object : X509ExtendedKeyManager() {
            override fun getCertificateChain(alias: String?): Array<X509Certificate> {
                android.util.Log.i("Pairing", "getCertificateChain alias=$alias")
                return arrayOf(cert)
            }
            override fun getPrivateKey(alias: String?): PrivateKey = keys.private
            // Claim the identity only when the peer asks for an RSA key: the
            // CertificateRequest names the accepted key types, and answering
            // it with a mismatched key makes Conscrypt send an empty chain,
            // which the device rejects right after the handshake.
            override fun getClientAliases(keyType: String?, issuers: Array<Principal>?): Array<String>? =
                if (keyType == null || keyType.equals("RSA", ignoreCase = true)) arrayOf("pa") else null

            override fun chooseClientAlias(keyType: Array<String>?, issuers: Array<Principal>?, socket: Socket?): String? {
                android.util.Log.i("Pairing", "chooseClientAlias keyTypes=${keyType?.joinToString(",")}")
                return if (keyType == null || keyType.any { it.equals("RSA", ignoreCase = true) }) "pa" else null
            }
            override fun getServerAliases(keyType: String?, issuers: Array<Principal>?): Array<String>? = null
            override fun chooseServerAlias(keyType: String?, issuers: Array<Principal>?, socket: Socket?): String? = null
        }
        // Trust all for the same reason as AdbShell: adbd's certificate is
        // self-signed and SPAKE2 with the pairing code is the real check.
        val trustAll = arrayOf<TrustManager>(object : javax.net.ssl.X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        })
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(arrayOf(km), trustAll, SecureRandom())
        return ctx
    }

    fun pair(
        host: String,
        port: Int,
        code: String,
        identity: KeyPair? = null,
        name: String = "phoneagent",
        timeoutMs: Int = 20000,
        storeDir: String? = null,
    ): Result {
        if (code.isBlank()) return Result(false, "配对码为空")
        return try {
            // Reuse the stored identity when there is one: every pairing must
            // present the same key, otherwise the device ends up holding a key
            // we no longer have and rejects our certificate.
            val keys = identity ?: loadOrCreateIdentity(storeDir)
            // Reuse the stored certificate when there is one: adbd appears to
            // match the certificate itself, so re-signing it on every pairing
            // (new serial, new validity) can never match what the device saw.
            val cert = loadOrCreateCert(keys, storeDir)
            fun pem(label: String, der: ByteArray): ByteArray {
                val b64 = Base64.encodeToString(der, Base64.NO_WRAP)
                    .chunked(64).joinToString("\n")
                return "-----BEGIN $label-----\n$b64\n-----END $label-----\n".toByteArray()
            }
            val certPem = pem("CERTIFICATE", cert.encoded)
            val keyPem = pem("PRIVATE KEY", keys.private.encoded)
            android.util.Log.i("Pairing", "cert=" + Base64.encodeToString(cert.encoded, Base64.NO_WRAP))
            val raw = Socket()
            raw.tcpNoDelay = true
            raw.connect(InetSocketAddress(host, port), timeoutMs)
            raw.soTimeout = timeoutMs
            val ssl = context(keys, cert).socketFactory.createSocket(raw, host, port, true) as SSLSocket
            ssl.useClientMode = true
            ssl.startHandshake()

            // pairing password = code || 64 bytes of TLS keying material
            // BoringSSL passes the label as sizeof("adb-label"), i.e. including
            // its NUL; Conscrypt may or may not preserve that, so try both and
            // keep whichever the device accepts.
            val labeled = runCatching {
                android.net.ssl.SSLSockets.exportKeyingMaterial(ssl, "adb-label\u0000", null, 64)
            }.getOrNull()
            val plain = android.net.ssl.SSLSockets.exportKeyingMaterial(ssl, "adb-label", null, 64)
            // AOSP passes sizeof(kExportedKeyLabel) = 10, i.e. the label with
            // its trailing NUL. Conscrypt does forward the NUL (the two calls
            // return different bytes), so use that form.
            val exported = (labeled ?: plain) ?: return Result(false, "TLS exporter returned nothing")
            fun hex(x: ByteArray?) = x?.joinToString("") { "%02x".format(it) } ?: "null"
            android.util.Log.i("Pairing", "exporter nul=${hex(labeled)}")
            android.util.Log.i("Pairing", "exporter plain=${hex(plain)}")
            // the exported material is raw binary; decoding it as text would
            // replace invalid UTF-8 sequences and change the password bytes
            val password = code.toByteArray() + exported

            val input = ssl.inputStream
            val output = ssl.outputStream
            android.util.Log.i("Pairing", "TLS ready, code=${code.length}B exported=${exported.size}B")
            // Compute first, then send header and payload together: with the
            // dialog kept alive (foreground service) the device no longer drops
            // the connection during the arithmetic, and splitting the packet
            // only made its payload read time out.
            val t0 = System.currentTimeMillis()
            val spake = Spake25519(password, true)
            val myMsg = spake.start()
            android.util.Log.i("Pairing", "spake2 msg ready in ${System.currentTimeMillis() - t0}ms")
            PairingFraming.write(output, PairingFraming.MSG_SPAKE2, myMsg)
            android.util.Log.i("Pairing", "wrote SPAKE2 packet (type=${PairingFraming.MSG_SPAKE2})")
            android.util.Log.i("Pairing", "waiting for device SPAKE2…")
            val (kind, theirMsg) = PairingFraming.read(input)
            // the device sends SPAKE2_MSG as 0 even though it accepts 1 from us
            android.util.Log.i("Pairing", "got packet type=$kind size=${theirMsg.size}")
            val keyMaterial = spake.finish(theirMsg)

            val cipher = PairingCipher(keyMaterial)
            val pub = keys.public as java.security.interfaces.RSAPublicKey
            val publicKeyLine = PairingRecords.adbPublicKey(pub.modulus, pub.publicExponent, name)
            PairingFraming.write(output, PairingFraming.MSG_PEER_INFO,
                cipher.encrypt(PairingRecords.packPeerInfo(publicKeyLine)))
            val (kind2, enc) = PairingFraming.read(input)
            android.util.Log.i("Pairing", "got peer info type=$kind2 size=${enc.size}")
            val (type, data) = PairingRecords.unpackPeerInfo(cipher.decrypt(enc))
            ssl.close()
            if (storeDir != null) {
                // hand the identity to Python so it can drive adbd itself
                val py = com.chaquo.python.Python.getInstance()
                py.getModule("phoneagent.ondevice").callAttr(
                    "save_identity", storeDir,
                    certPem,
                    keyPem,
                )
                android.util.Log.i("Pairing", "identity stored in $storeDir")
            }
            Result(true, "配对成功，type=$type 设备信息=${data.take(60)}")
        } catch (t: Throwable) {
            Result(false, "${t.javaClass.simpleName}: ${t.message}")
        }
    }
}
