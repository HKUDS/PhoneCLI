package com.phoneagent.app

import android.annotation.SuppressLint
import android.util.Log
import android.webkit.WebView
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * The page, from Python.
 *
 * The accessibility tree of a WebView on a secondary display comes back as one
 * empty node (measured), so reading a page through the service is not an
 * option. `evaluateJavascript` is, and it is better than a screenshot anyway:
 * the text is exact, the structure is real, and a click can name its target.
 *
 * Every call is started and polled rather than awaited -- the same shape as the
 * accessibility bridge, for the same reason: a Java call that blocks holds the
 * interpreter.
 */
@SuppressLint("StaticFieldLeak")   // cleared in detach(); see the comment there
object BrowserBridge {

    private const val TAG = "Pairing"

    @Volatile
    private var web: WebView? = null

    private val results = ConcurrentHashMap<Int, String>()
    private val evalSeq = AtomicInteger(0)

    @Volatile
    private var loadedUrl: String = ""
    @Volatile
    private var loading: Boolean = false
    @Volatile
    private var loadStartedAt: Long = 0L

    /** A page that never finishes must not leave the bridge waiting for ever. */
    private const val LOAD_TIMEOUT_MS = 25_000L

    fun attach(view: WebView) {
        web = view
    }

    /**
     * Called from onDestroy. The view is the activity's, so holding it after the
     * activity is gone keeps the whole window alive; lint is right to flag the
     * static field. Dropping it here is what makes the static reference safe.
     */
    fun detach() {
        web = null
        results.clear()
        loadedUrl = ""
        loading = false
    }

    /** Run ``js`` on the page; poll [pollEval] with the returned tag. */
    @JvmStatic
    fun beginEval(js: String): Int {
        val tag = evalSeq.incrementAndGet()
        val view = web
        if (view == null) {
            results[tag] = "\u0000no-webview"
            return tag
        }
        view.post {
            try {
                view.evaluateJavascript(js, { value ->
                    results[tag] = value ?: "null"
                })
            } catch (t: Throwable) {
                results[tag] = "\u0000" + (t.message ?: "eval failed")
            }
        }
        return tag
    }

    /** The result, or "" while the page is still answering. */
    @JvmStatic
    fun pollEval(tag: Int): String {
        val value = results[tag] ?: return ""
        results.remove(tag)
        return value
    }

    /**
     * Drop a result nobody will read.
     *
     * Python stops polling when its own deadline expires; the map entry for that
     * tag would otherwise stay for the life of the process, one per timed-out
     * eval.
     */
    @JvmStatic
    fun discardEval(tag: Int) {
        results.remove(tag)
    }

    @JvmStatic
    fun beginLoad(url: String) {
        val view = web ?: return
        loading = true
        loadedUrl = ""
        loadStartedAt = System.currentTimeMillis()
        lastUrl = url
        view.post { view.loadUrl(url) }
        Log.i(TAG, "browser: load $url")
    }

    /** "" while loading, "error: …" on failure, else the loaded url. */
    @JvmStatic
    fun pollLoad(): String {
        if (loading) {
            if (System.currentTimeMillis() - loadStartedAt > LOAD_TIMEOUT_MS) {
                loading = false
                loadedUrl = "timeout: " + lastUrl
            } else {
                return ""
            }
        }
        return loadedUrl
    }

    /**
     * Wrap fetch and XMLHttpRequest so the data a page receives can be read.
     *
     * The text of a page is what it chose to show; the responses it fetched are
     * often the structured version of the same thing (a price list, a result
     * array), which is worth more than parsing the rendered words.
     *
     * Injected on every page start. A page that installs its own transport
     * after load will not be captured -- a known limit.
     */
    const val NET_SHIM = """
(function(){
  try {
    if (window.__paNetInstalled) return 'already';
    window.__paNetInstalled = true;
    window.__paNet = [];
    function record(url, status, body) {
      try {
        var text = (typeof body === 'string') ? body : '';
        window.__paNet.push({url: String(url || '').slice(0, 300),
                             status: status || 0,
                             body: text.slice(0, 4000)});
        if (window.__paNet.length > 25) window.__paNet.shift();
      } catch (e) {}
    }
    var origFetch = window.fetch;
    if (origFetch) {
      window.fetch = function() {
        var args = arguments;
        var url = (args[0] && args[0].url) ? args[0].url : args[0];
        return origFetch.apply(this, args).then(function(resp) {
          try {
            resp.clone().text().then(function(t) { record(url, resp.status, t); })
                 .catch(function(){});
          } catch (e) {}
          return resp;
        });
      };
    }
    var OrigXHR = window.XMLHttpRequest;
    if (OrigXHR) {
      var origOpen = OrigXHR.prototype.open;
      var origSend = OrigXHR.prototype.send;
      OrigXHR.prototype.open = function(m, u) {
        this.__paUrl = u;
        return origOpen.apply(this, arguments);
      };
      OrigXHR.prototype.send = function() {
        var xhr = this;
        try {
          xhr.addEventListener('load', function() {
            try { record(xhr.__paUrl, xhr.status, xhr.responseText); } catch (e) {}
          });
        } catch (e) {}
        return origSend.apply(this, arguments);
      };
    }
    return 'installed';
  } catch (e) { return 'failed'; }
})()
"""

    /** Install the capture shim on the current page. */
    @JvmStatic
    fun installNetworkCapture() {
        val view = web ?: return
        view.post { view.evaluateJavascript(NET_SHIM, null) }
    }

    /** Called by the activity. */
    fun onPageFinished(url: String, error: String) {
        lastUrl = url
        loading = false
        loadedUrl = if (error.isEmpty()) url else "error: $error"
        Log.i(TAG, "browser: finished $loadedUrl")
    }

    @Volatile
    private var lastUrl: String = ""

    @JvmStatic
    fun currentUrl(): String = lastUrl
}
