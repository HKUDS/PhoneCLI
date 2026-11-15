package com.phoneagent.app.pairing

import java.math.BigInteger

/**
 * Deterministic SPAKE2 run with fixed inputs, so the port can be compared
 * against the Python implementation (which the RFC 8032 vectors already
 * validated) byte for byte, without needing a pairing window.
 */
object SelfTest {
    fun run(): String {
        val pw = ByteArray(70) { it.toByte() }
        val a = Spake25519(pw, true, BigInteger.ONE.shiftLeft(200))
        val b = Spake25519(pw, false, BigInteger.ONE.shiftLeft(201))
        val ma = a.start()
        val mb = b.start()
        val ka = a.finish(mb)
        val kb = b.finish(ma)
        fun hex(x: ByteArray) = x.joinToString("") { "%02x".format(it) }
        return "MSG_CLIENT=${hex(ma)} MSG_SERVER=${hex(mb)} KEY_CLIENT=${hex(ka)} AGREE=${ka.contentEquals(kb)}"
    }
}
