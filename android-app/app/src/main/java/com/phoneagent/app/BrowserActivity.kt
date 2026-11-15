package com.phoneagent.app

import android.app.Activity
import android.annotation.SuppressLint
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * A page the agent can read and drive.
 *
 * The agent already sees whatever app is on its display, but only as pixels and
 * accessibility nodes. A WebView it owns is different: the text, the structure
 * and the cookies are all reachable from Python, so reading a page stops being
 * a screenshot problem.
 *
 *   adb shell am start --display <id> -n com.phoneagent.app/.BrowserActivity \
 *       --es url https://example.com [--ez desktop true]
 *
 * This file exists first as an experiment: does a WebView render at all on a
 * virtual display that is captured through an ImageReader?
 */
class BrowserActivity : Activity() {

    private var web: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = intent?.getStringExtra("url") ?: "https://example.com"
        val desktop = intent?.getBooleanExtra("desktop", true) ?: true

        val view = WebView(this)
        view.layoutParams = ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT
        )
        view.settings.apply {
            // Needed: the agent reads a page with evaluateJavascript, and most
            // pages render their content with JS anyway. Nothing of ours is
            // downloaded into this WebView -- no addJavascriptInterface, no
            // file access -- so page script can act inside the page and no
            // further.
            @SuppressLint("SetJavaScriptEnabled")
            javaScriptEnabled = true
            domStorageEnabled = true
            loadsImagesAutomatically = true
            useWideViewPort = true
            loadWithOverviewMode = true
            if (desktop) {
                // Sites serve their desktop layout, which is the one the agent
                // can actually operate: no "open in the app" interstitial.
                userAgentString =
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            }
        }
        view.webViewClient = object : WebViewClient() {
            override fun onPageStarted(v: WebView, url: String, favicon: android.graphics.Bitmap?) {
                super.onPageStarted(v, url, favicon)
                // Before the page's own scripts run is the only chance to
                // wrap fetch/XHR; anything installed later misses the calls.
                BrowserBridge.installNetworkCapture()
            }

            override fun onPageFinished(v: WebView, loaded: String) {
                super.onPageFinished(v, loaded)
                BrowserBridge.onPageFinished(loaded, "")
                Log.i("Pairing", "browser: loaded $loaded title=${v.title}")
            }

            override fun onReceivedError(
                v: WebView,
                request: android.webkit.WebResourceRequest,
                error: android.webkit.WebResourceError,
            ) {
                super.onReceivedError(v, request, error)
                BrowserBridge.onPageFinished(request.url.toString(),
                    error.description?.toString() ?: "load failed")
            }
        }
        // A WebView with accessibility off reports one empty node to the
        // service that reads the tree, which is the same as seeing nothing.
        view.importantForAccessibility = android.view.View.IMPORTANT_FOR_ACCESSIBILITY_YES
        setContentView(view)
        view.requestFocus()
        web = view
        BrowserBridge.attach(view)
        if (androidx.webkit.WebViewFeature.isFeatureSupported(
                androidx.webkit.WebViewFeature.DOCUMENT_START_SCRIPT)) {
            androidx.webkit.WebViewCompat.addDocumentStartJavaScript(
                view, BrowserBridge.NET_SHIM, setOf("*"))
            Log.i("Pairing", "browser: capture shim runs at document start")
        }
        Log.i("Pairing", "browser: opening $url desktop=$desktop")
        view.loadUrl(url)
    }

    override fun onDestroy() {
        BrowserBridge.detach()
        web?.destroy()
        web = null
        super.onDestroy()
    }
}
