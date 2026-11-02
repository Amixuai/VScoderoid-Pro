package com.vscodroid

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import com.vscodroid.util.Logger
import com.vscodroid.util.drawBehindSystemBars
import com.vscodroid.util.padForSystemBars

/**
 * The only address Desktop Mode's screen is ever loaded from.
 *
 * `desktop.py` starts a VNC server and a noVNC page inside the Debian system on
 * this device and prints this address with a one-time password in the query. Both
 * the bridge, deciding whether to open the page here instead of in a browser, and
 * the activity, deciding whether to load it at all, ask this one question, so
 * they cannot disagree. Plain values rather than a [Uri] so a JVM test can ask it.
 */
internal object DesktopAddress {
    const val PORT = 6080
    const val PATH = "/vnc.html"

    fun matches(scheme: String?, host: String?, port: Int, path: String?): Boolean =
        scheme == "http" && host == "127.0.0.1" && port == PORT && path == PATH

    fun matches(uri: Uri?): Boolean = uri != null && matches(uri.scheme, uri.host, uri.port, uri.path)
}

/**
 * Shows the desktop (Electron) VSCodium that Desktop Mode runs, inside the app.
 *
 * Not exported and with no launcher entry: [com.vscodroid.bridge.AndroidBridge.openExternalUrl]
 * starts it, by name, when the address `desktop.py start` prints reaches it
 * through `xdg-open`. Anything else it is handed is refused and the screen closes.
 *
 * The page is the noVNC viewer, served from 127.0.0.1 by the Debian system. The
 * view may go nowhere else: navigation off that origin is dropped, file and content
 * access are off, and nothing is cached, because the address carries the session
 * password. The address is never logged, for the same reason.
 *
 * The screen stays on while this is open, since the desktop is not something a
 * person looks at for ten seconds at a time. Back closes the screen and leaves the
 * desktop running; it is stopped with `desktop.py stop`.
 */
class DesktopActivity : AppCompatActivity() {
    private val tag = "DesktopActivity"
    private var webView: WebView? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val address = intent?.data
        if (!DesktopAddress.matches(address)) {
            Logger.w(tag, "Refused to open a page that is not the desktop viewer")
            finish()
            return
        }
        drawBehindSystemBars()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val view = WebView(this).apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                setSupportMultipleWindows(false)
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                cacheMode = WebSettings.LOAD_NO_CACHE
            }
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                    val target = request.url
                    // noVNC stays on its own origin; refuse the page everything else.
                    return !(target.scheme == "http" && target.host == "127.0.0.1" && target.port == DesktopAddress.PORT)
                }

                override fun onRenderProcessGone(v: WebView, detail: RenderProcessGoneDetail): Boolean {
                    Logger.w(this@DesktopActivity.tag, "The viewer's render process ended; closing the screen")
                    finish()
                    return true
                }
            }
        }
        webView = view

        val root = FrameLayout(this).apply {
            addView(view, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            padForSystemBars()
        }
        setContentView(root)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = finish()
        })

        // Only on a fresh screen: a rotation or a return from the background keeps
        // the page, and reloading it would drop the session.
        if (savedInstanceState == null) view.loadUrl(address.toString())
        view.requestFocus()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView?.saveState(outState)
    }

    override fun onDestroy() {
        webView?.let { v ->
            (v.parent as? ViewGroup)?.removeView(v)
            v.destroy()
        }
        webView = null
        super.onDestroy()
    }
}
