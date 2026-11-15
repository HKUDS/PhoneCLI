package com.phoneagent.app.pairing

import java.math.BigInteger
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Ed25519 group arithmetic in extended coordinates.
 *
 * A hand-written port of the arithmetic in phoneagent/devices/pairing/ed25519.py
 * (itself verified against the RFC 8032 test vector). Pairing runs it a few
 * hundred times, so BigInteger is fast enough and far easier to audit.
 */
object Ed25519 {
    val P: BigInteger = BigInteger.TWO.pow(255).subtract(BigInteger.valueOf(19))
    val L: BigInteger = BigInteger.TWO.pow(252)
        .add(BigInteger("27742317777372353535851937790883648493"))
    private val D: BigInteger = BigInteger.valueOf(-121665).mod(P)
        .multiply(BigInteger.valueOf(121666).modInverse(P)).mod(P)
    private val SQRT_M1: BigInteger = BigInteger.TWO.modPow(P.subtract(BigInteger.ONE).divide(BigInteger.valueOf(4)), P)
    val BASE: ByteArray = hex("5866666666666666666666666666666666666666666666666666666666666666")

    class Point(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger) {
        companion object { val IDENTITY = Point(BigInteger.ZERO, BigInteger.ONE, BigInteger.ONE, BigInteger.ZERO) }
    }

    fun decode(data: ByteArray): Point {
        require(data.size == 32) { "an ed25519 point is 32 bytes" }
        var y = BigInteger(1, data.reversedArray())
        val sign = if (data[31].toInt() and 0x80 != 0) BigInteger.ONE else BigInteger.ZERO
        y = y.and(BigInteger.TWO.pow(255).subtract(BigInteger.ONE))
        require(y < P) { "y out of range" }
        val y2 = y.multiply(y).mod(P)
        val x2 = y2.subtract(BigInteger.ONE).multiply(D.multiply(y2).add(BigInteger.ONE).mod(P).modInverse(P)).mod(P)
        var x = x2.modPow(P.add(BigInteger.valueOf(3)).divide(BigInteger.valueOf(8)), P)
        if (x.multiply(x).subtract(x2).mod(P) != BigInteger.ZERO) x = x.multiply(SQRT_M1).mod(P)
        require(x.multiply(x).subtract(x2).mod(P) == BigInteger.ZERO) { "point is not on the curve" }
        if (x.testBit(0) != (sign == BigInteger.ONE)) x = P.subtract(x)
        return Point(x, y, BigInteger.ONE, x.multiply(y).mod(P))
    }

    fun encode(p: Point): ByteArray {
        val zi = p.z.modInverse(P)
        val x = p.x.multiply(zi).mod(P)
        val y = p.y.multiply(zi).mod(P)
        var v = y
        if (x.testBit(0)) v = v.setBit(255)
        return bigToLe32(v)
    }

    fun add(a: Point, b: Point): Point {
        val x1 = a.x; val y1 = a.y; val z1 = a.z; val t1 = a.t
        val x2 = b.x; val y2 = b.y; val z2 = b.z; val t2 = b.t
        val aa = y1.subtract(x1).multiply(y2.subtract(x2)).mod(P)
        val bb = y1.add(x1).multiply(y2.add(x2)).mod(P)
        val cc = t1.multiply(BigInteger.TWO).multiply(D).multiply(t2).mod(P)
        val dd = z1.multiply(BigInteger.TWO).multiply(z2).mod(P)
        val e = bb.subtract(aa); val f = dd.subtract(cc); val g = dd.add(cc); val h = bb.add(aa)
        return Point(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P))
    }

    fun double(a: Point): Point {
        val aa = a.x.multiply(a.x).mod(P)
        val bb = a.y.multiply(a.y).mod(P)
        val cc = BigInteger.TWO.multiply(a.z).multiply(a.z).mod(P)
        val h = aa.add(bb)
        val e = h.subtract(a.x.add(a.y).multiply(a.x.add(a.y)).mod(P))
        val g = aa.subtract(bb)
        val f = cc.add(g)
        return Point(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P))
    }

    fun negate(a: Point) = Point(P.subtract(a.x).mod(P), a.y, a.z, P.subtract(a.t).mod(P))

    fun multiply(k: BigInteger, p: Point): Point {
        if (k.signum() == 0) return Point.IDENTITY
        if (k.signum() < 0) return multiply(k.negate(), negate(p))
        var result = Point.IDENTITY
        var addend = p
        var scalar = k
        while (scalar.signum() > 0) {
            if (scalar.testBit(0)) result = add(result, addend)
            addend = double(addend)
            scalar = scalar.shiftRight(1)
        }
        return result
    }

    fun multiplyBase(k: BigInteger) = multiply(k, decode(BASE))

    /** Little-endian bytes of v padded to exactly [size] bytes. */
    fun bigToLe(v: BigInteger, size: Int): ByteArray {
        val out = ByteArray(size)
        val be = v.toByteArray()
        var i = 0
        while (i < size && i < be.size) { out[i] = be[be.size - 1 - i]; i++ }
        return out
    }

    fun bigToLe32(v: BigInteger): ByteArray {
        val out = ByteArray(32)
        val be = v.toByteArray()
        var i = 0
        while (i < 32 && i < be.size) { out[i] = be[be.size - 1 - i]; i++ }
        return out
    }

    fun leToBig(b: ByteArray) = BigInteger(1, b.reversedArray())
}

/**
 * BoringSSL's SPAKE2 variant (crypto/curve25519/spake25519.cc), not the IRTF
 * draft. Port of phoneagent/devices/pairing/spake25519.py.
 */
class Spake25519(private val password: ByteArray, private val isClient: Boolean, fixedX: BigInteger? = null) {
    private val passwordHash: ByteArray = MessageDigest.getInstance("SHA-512").digest(password)
    private val w: BigInteger
    private val x: BigInteger
    private var myMsg: ByteArray? = null

    init {
        var mask = Ed25519.leToBig(passwordHash).mod(Ed25519.L)
        if (mask.testBit(0)) mask = mask.add(Ed25519.L)
        if (mask.testBit(1)) mask = mask.add(Ed25519.L.shiftLeft(1))
        if (mask.testBit(2)) mask = mask.add(Ed25519.L.shiftLeft(2))
        w = mask
        x = if (fixedX != null) fixedX.shiftLeft(3) else {
            val rnd = ByteArray(64); SecureRandom().nextBytes(rnd)
            Ed25519.leToBig(rnd).mod(Ed25519.L).shiftLeft(3)
        }
    }

    fun start(): ByteArray {
        val maskPoint = Ed25519.multiply(w, if (isClient) M_POINT else N_POINT)
        val own = Ed25519.add(Ed25519.multiply(x, Ed25519.decode(Ed25519.BASE)), maskPoint)
        return Ed25519.encode(own).also { myMsg = it }
    }

    fun finish(theirMsg: ByteArray): ByteArray {
        val mine = myMsg ?: error("start() first")
        val theirPoint = Ed25519.decode(theirMsg)
        val peerMask = Ed25519.multiply(w, if (isClient) N_POINT else M_POINT)
        val unmasked = Ed25519.add(theirPoint, Ed25519.negate(peerMask))
        val shared = Ed25519.encode(Ed25519.multiply(x, unmasked))

        val sha = MessageDigest.getInstance("SHA-512")
        fun field(b: ByteArray) {
            sha.update(bigToLe64(BigInteger.valueOf(b.size.toLong())))
            sha.update(b)
        }
        if (isClient) {
            field(CLIENT_NAME); field(SERVER_NAME); field(mine); field(theirMsg)
        } else {
            field(CLIENT_NAME); field(SERVER_NAME); field(theirMsg); field(mine)
        }
        field(shared); field(passwordHash)
        // SPAKE2_MAX_KEY_SIZE is 64: the whole SHA-512 digest is handed to
        // the AES layer, which HKDFs it down to 16 bytes
        return sha.digest()
    }

    companion object {
        val CLIENT_NAME = "adb pair client\u0000".toByteArray()
        val SERVER_NAME = "adb pair server\u0000".toByteArray()
        val M_POINT: Ed25519.Point = Ed25519.decode(hex("5ada7e4bf6ddd9adb6626d32131c6b5c51a1e347a3478f53cfcf441b88eed12e"))
        val N_POINT: Ed25519.Point = Ed25519.decode(hex("10e3df0ae37d8e7a99b5fe74b44672103dbddcbd06af680d71329a11693bc778"))
        fun bigToLe64(v: BigInteger): ByteArray {
            val out = ByteArray(8)
            val be = v.toByteArray()
            var i = 0
            while (i < 8 && i < be.size) { out[i] = be[be.size - 1 - i]; i++ }
            return out
        }
    }
}

/** HKDF-SHA256 + AES-128-GCM with the zero nonce and per-message counter. */
class PairingCipher(keyMaterial: ByteArray) {
    private val aes = hkdf(keyMaterial, "adb pairing_auth aes-128-gcm key".toByteArray(), 16)
    private var encSeq = 0L
    private var decSeq = 0L

    fun encrypt(data: ByteArray): ByteArray = seal(aes, encSeq++, data)
    fun decrypt(blob: ByteArray): ByteArray = open(aes, decSeq++, blob)

    private fun nonce(seq: Long): ByteArray {
        val n = ByteArray(12)
        for (i in 0 until 8) n[i] = ((seq ushr (8 * i)) and 0xff).toByte()
        return n
    }

    private fun seal(key: ByteArray, seq: Long, data: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce(seq)))
        return c.doFinal(data)
    }

    private fun open(key: ByteArray, seq: Long, blob: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce(seq)))
        return c.doFinal(blob)
    }

    companion object {
        fun hkdf(ikm: ByteArray, info: ByteArray, length: Int): ByteArray {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(ByteArray(32), "HmacSHA256"))   // empty salt
            val prk = mac.doFinal(ikm)
            mac.init(SecretKeySpec(prk, "HmacSHA256"))
            val out = ByteArray(length)
            var t = ByteArray(0)
            var pos = 0
            var counter = 1
            while (pos < length) {
                mac.update(t); mac.update(info); mac.update(counter.toByte())
                t = mac.doFinal()
                val n = minOf(t.size, length - pos)
                System.arraycopy(t, 0, out, pos, n)
                pos += n; counter++
            }
            return out
        }
    }
}

/** The 8192-byte PeerInfo record and the 524-byte adb public key blob. */
object PairingRecords {
    const val PEER_INFO_SIZE = 8192
    const val TYPE_RSA_PUBLIC_KEY = 0

    fun packPeerInfo(data: String, type: Int = TYPE_RSA_PUBLIC_KEY): ByteArray {
        val out = ByteArray(PEER_INFO_SIZE)
        out[0] = type.toByte()
        val bytes = data.toByteArray()
        require(bytes.size <= PEER_INFO_SIZE - 1) { "peer info too large" }
        System.arraycopy(bytes, 0, out, 1, bytes.size)
        return out
    }

    fun unpackPeerInfo(blob: ByteArray): Pair<Int, String> {
        require(blob.size == PEER_INFO_SIZE) { "peer info must be ${PEER_INFO_SIZE} bytes" }
        val end = (1 until blob.size).firstOrNull { blob[it] == 0.toByte() } ?: blob.size
        return Pair(blob[0].toInt(), String(blob, 1, end - 1))
    }

    fun adbPublicKey(modulus: BigInteger, exponent: BigInteger, name: String): String {
        // the modulus and rr are 256 bytes each: a 32-byte converter would
        // truncate them and the device would store a key we can never match
        val n = Ed25519.bigToLe(modulus, 256)
        val r = BigInteger.ONE.shiftLeft(2048)
        val n0inv = modulus.modInverse(BigInteger.ONE.shiftLeft(32)).negate().mod(BigInteger.ONE.shiftLeft(32))
        val rr = r.multiply(r).mod(modulus)
        val blob = java.io.ByteArrayOutputStream()
        blob.write(Ed25519.bigToLe(BigInteger.valueOf(64), 4))          // modulus in 32-bit words
        blob.write(Ed25519.bigToLe(n0inv, 4))
        blob.write(n)
        blob.write(Ed25519.bigToLe(rr, 256))
        blob.write(Ed25519.bigToLe(exponent, 4))
        val b64 = android.util.Base64.encodeToString(blob.toByteArray(), android.util.Base64.NO_WRAP)
        return "$b64 ${name.replace(Regex("\\s"), "_")}"
    }
}

fun hex(s: String): ByteArray = ByteArray(s.length / 2) {
    ((Character.digit(s[it * 2], 16) shl 4) or Character.digit(s[it * 2 + 1], 16)).toByte()
}

/** Pairing packet framing: version, type, big-endian length. */
object PairingFraming {
    const val VERSION = 1
    // wireshark-verified by the reference client: 0 = SPAKE2, 1 = PEER_INFO
    const val MSG_SPAKE2 = 0
    const val MSG_PEER_INFO = 1

    fun writeHeader(out: java.io.OutputStream, type: Int, size: Int) {
        val header = ByteArray(6)
        header[0] = VERSION.toByte(); header[1] = type.toByte()
        header[2] = (size ushr 24).toByte(); header[3] = (size ushr 16).toByte()
        header[4] = (size ushr 8).toByte(); header[5] = size.toByte()
        out.write(header); out.flush()
    }

    fun write(out: java.io.OutputStream, type: Int, payload: ByteArray) {
        val header = ByteArray(6)
        header[0] = VERSION.toByte(); header[1] = type.toByte()
        header[2] = (payload.size ushr 24).toByte(); header[3] = (payload.size ushr 16).toByte()
        header[4] = (payload.size ushr 8).toByte(); header[5] = payload.size.toByte()
        out.write(header); out.write(payload); out.flush()
    }

    fun read(input: java.io.InputStream): Pair<Int, ByteArray> {
        val header = ByteArray(6)
        if (input.read(header) != 6) throw java.io.EOFException("pairing connection closed")
        require(header[0].toInt() == VERSION) { "pairing header version mismatch" }
        val len = ((header[2].toInt() and 0xff) shl 24) or ((header[3].toInt() and 0xff) shl 16) or
                ((header[4].toInt() and 0xff) shl 8) or (header[5].toInt() and 0xff)
        require(len in 1..16384) { "bad payload length $len" }
        val payload = ByteArray(len)
        var read = 0
        while (read < len) {
            val n = input.read(payload, read, len - read)
            if (n < 0) throw java.io.EOFException("short payload")
            read += n
        }
        return Pair(header[1].toInt(), payload)
    }
}
