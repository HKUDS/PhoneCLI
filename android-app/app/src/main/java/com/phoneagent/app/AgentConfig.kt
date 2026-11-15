package com.phoneagent.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Where the model settings live.
 *
 * The API key is a credential, so it does not go into `agent.json` in the
 * clear: it is encrypted with an AES/GCM key that lives in the Android
 * Keystore and never leaves it. Everything else (base URL, model, mock script)
 * is ordinary configuration and stays readable.
 *
 * `agent.json` used to hold the key. [load] migrates such a file on sight and
 * rewrites it without the key, so an existing install upgrades silently.
 */
object AgentConfig {

    private const val TAG = "Pairing"
    private const val CONFIG_FILE = "agent.json"
    private const val SECRET_FILE = "agent.secret"
    private const val KEY_ALIAS = "phoneagent.api_key"
    private const val GCM_TAG_BITS = 128

    data class Config(
        val apiKey: String,
        val apiBase: String,
        val model: String,
        val visionModel: String,
        /** Steps per task; 0 means the agent's own default. */
        val maxRounds: Int,
        val mock: String,
    ) {
        val hasKey: Boolean get() = apiKey.isNotEmpty()
    }

    // -- reading ----------------------------------------------------------

    @JvmStatic
    fun load(context: Context): Config {
        val json = readJson(context)
        var key = readSecret(context)
        val legacy = json.optString("api_key", "")
        if (legacy.isNotEmpty() && legacy != key) {
            // The file wins: it is how a key gets replaced when the stored one
            // belongs to another provider (switching from OpenRouter to DeepSeek
            // left the old key in the keystore, and every request went out with
            // it). Written to the secret and then stripped from the file.
            key = legacy
            writeSecret(context, key)
            rewriteJson(context, json)
            Log.i(TAG, "migrated the api key out of $CONFIG_FILE")
        }
        return Config(
            apiKey = key,
            apiBase = json.optString("api_base", "https://openrouter.ai/api/v1"),
            model = json.optString("model", "qwen/qwen3.8-flash"),
            // empty means "the same as the text model"
            visionModel = json.optString("vision_model", ""),
            maxRounds = json.optInt("max_rounds", 0),
            mock = json.optString("mock", ""),
        )
    }

    @JvmStatic
    fun hasKey(context: Context): Boolean = load(context).hasKey

    // -- writing ----------------------------------------------------------

    @JvmStatic
    fun save(context: Context, apiKey: String, apiBase: String, model: String,
             visionModel: String = "", maxRounds: Int = 0, mock: String = "") {
        if (apiKey.isEmpty()) {
            clearSecret(context)
        } else {
            writeSecret(context, apiKey)
        }
        val json = JSONObject()
            .put("api_base", apiBase.ifEmpty { "https://openrouter.ai/api/v1" })
            .put("model", model.ifEmpty { "qwen/qwen3.8-flash" })
            .put("vision_model", visionModel)
            .put("max_rounds", maxRounds)
        if (mock.isNotEmpty()) json.put("mock", mock)
        writePrivate(context, json.toString())
    }

    // -- plain config -----------------------------------------------------

    private fun configFile(context: Context) = File(context.filesDir, CONFIG_FILE)

    /**
     * Write the config readable by this app alone.
     *
     * `File.writeText` creates the file with the process umask, and on this
     * phone that produced 0666: agent.json was world-writable, so any installed
     * app could have pointed api_base at its own server and collected the key on
     * the next test. openFileOutput(MODE_PRIVATE) closes that.
     */
    private fun writePrivate(context: Context, text: String) {
        context.openFileOutput(CONFIG_FILE, Context.MODE_PRIVATE).use { out ->
            out.write(text.toByteArray(Charsets.UTF_8))
        }
    }

    private fun readJson(context: Context): JSONObject {
        val file = configFile(context)
        if (!file.exists()) return JSONObject()
        return runCatching { JSONObject(file.readText()) }
            .onFailure { Log.i(TAG, "$CONFIG_FILE unreadable: ${it.message}") }
            .getOrDefault(JSONObject())
    }

    private fun rewriteJson(context: Context, json: JSONObject) {
        json.remove("api_key")
        writePrivate(context, json.toString())
    }

    // -- secret -----------------------------------------------------------

    private fun secretFile(context: Context) = File(context.filesDir, SECRET_FILE)

    private fun keystore(): KeyStore =
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun secretKey(): SecretKey {
        val store = keystore()
        (store.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    private fun writeSecret(context: Context, value: String) {
        runCatching {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, secretKey())
            val blob = cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            secretFile(context).writeBytes(blob)
        }.onFailure { Log.i(TAG, "cannot store the api key: ${it.message}") }
    }

    private fun readSecret(context: Context): String {
        val file = secretFile(context)
        if (!file.exists()) return ""
        return runCatching {
            val blob = file.readBytes()
            val iv = blob.copyOfRange(0, 12)      // GCM IV length used by JCE
            val body = blob.copyOfRange(12, blob.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(GCM_TAG_BITS, iv))
            String(cipher.doFinal(body), Charsets.UTF_8)
        }.onFailure {
            // a reinstalled app loses the keystore key; the ciphertext is then
            // useless and the user simply has to enter the key again
            Log.i(TAG, "stored api key unreadable: ${it.javaClass.simpleName}")
        }.getOrDefault("")
    }

    private fun clearSecret(context: Context) {
        runCatching { secretFile(context).delete() }
    }
}
