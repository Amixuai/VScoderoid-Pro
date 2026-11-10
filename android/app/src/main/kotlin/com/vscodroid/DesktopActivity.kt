package com.vscodroid

import android.annotation.SuppressLint
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.File
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
    private var lastViewerStatus = ""

    private companion object {
        /** Prefix for a status line in the diagnostic note; a developer aid, not interface text. */
        const val VIEWER_SAYS = "viewer: "
    }

    @SuppressLint("SetJavaScriptEnabled", "SetTextI18n")
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

        // What the viewer page reports when it cannot connect. noVNC says only "Failed to connect
        // to server"; the browser's own message (a refused handshake, a blocked request) says why.
        // Shown at the bottom, selectable so it can be copied, dismissed by a tap. The session
        // password is cut out of anything shown.
        val note = TextView(this).apply {
            setTextColor(Color.WHITE)
            setBackgroundColor(0xDD000000.toInt())
            textSize = 12f
            setPadding(24, 16, 24, 16)
            setTextIsSelectable(true)
            visibility = View.GONE
            setOnClickListener { visibility = View.GONE }
        }
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
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(message: ConsoleMessage): Boolean {
                    val level = message.messageLevel()
                    if ((level == ConsoleMessage.MessageLevel.ERROR || level == ConsoleMessage.MessageLevel.WARNING) &&
                        !message.message().contains("preload")
                    ) {
                        val shown = message.message().replace(Regex("password=[^&\\s'\"]*"), "password=...")
                        note.post {
                            showNote(note, shown)
                        }
                    }
                    return true
                }
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
            addView(note, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
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
        view.post { rememberScreen(view) }
        pollViewerStatus(view, note)
    }

    /**
     * What the noVNC page says about the connection ("Connecting", "Disconnected", ...), checked every
     * few seconds and added to the note when it changes. With automatic reconnect the page can keep
     * trying behind a spinner, and this is the only way to see that from outside the page.
     */
    @SuppressLint("SetTextI18n")
    private fun pollViewerStatus(view: WebView, note: TextView) {
        view.evaluateJavascript("(function(){var s=document.getElementById('noVNC_status');return s?s.textContent:''})()") { raw ->
            val text = raw?.trim('"').orEmpty()
            if (text.isNotEmpty() && text != "null" && text != lastViewerStatus) {
                lastViewerStatus = text
                showNote(note, VIEWER_SAYS + text)
            }
        }
        view.postDelayed({ if (webView === view) pollViewerStatus(view, note) }, 3000)
    }

        /**
     * Writes this phone's screen (the viewer's size in real pixels, and the display density) where
     * `desktop.py start` reads it, so the desktop is made exactly as many pixels as the screen has and
     * is sharp on any phone, whatever its resolution. Nothing here is sent anywhere; it is one small file.
     */
    private fun rememberScreen(view: WebView) {
        val w = view.width
        val h = view.height
        if (w < 200 || h < 200) return
        runCatching {
            val file = File(filesDir, "home/.vscodroid/screen.json")
            file.parentFile?.mkdirs()
            file.writeText(JSONObject().put("width", w).put("height", h).put("density", resources.displayMetrics.density.toDouble()).toString())
        }
    }

    private var hideNote: Runnable? = null

    /**
     * Shows a line in the note and takes the note away again after a few seconds, so a message like
     * "Connected" does not sit over the desktop for good. A new message restarts the wait.
     */
    private fun showNote(note: TextView, added: String) {
        val soFar = if (note.visibility == View.VISIBLE) note.text.toString() else String()
        note.text = appendNote(soFar, added)
        note.visibility = View.VISIBLE
        hideNote?.let { note.removeCallbacks(it) }
        val hide = Runnable { note.visibility = View.GONE }
        hideNote = hide
        note.postDelayed(hide, 6000)
    }

/** The viewer's messages, newest last, kept short enough to fit above the keyboard. */
    private fun appendNote(shownSoFar: String, added: String): String =
        (shownSoFar + System.lineSeparator() + added).trim().takeLast(700)

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
