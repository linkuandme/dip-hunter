package com.diphunter.app

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewFeature
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Dip Hunter — thin WebView client.
 *
 * Loads the GitHub-Pages-hosted dashboard, auto-reloads every
 * `R.integer.reload_interval_ms` ms (default 10 min), and provides
 * pull-to-refresh. The dashboard itself is rebuilt server-side every
 * 10 min by GitHub Actions.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var swipe:  SwipeRefreshLayout
    private lateinit var footer: TextView

    private val handler = Handler(Looper.getMainLooper())
    private var intervalMs: Long = 600_000          // overridden in onCreate
    private var dashboardUrl: String = ""
    private var lastReloadEpoch: Long = 0L

    private val reloadTask = object : Runnable {
        override fun run() {
            if (!isFinishing && !isDestroyed) {
                webView.reload()
                lastReloadEpoch = System.currentTimeMillis()
                updateFooter()
            }
            handler.postDelayed(this, intervalMs)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        intervalMs   = resources.getInteger(R.integer.reload_interval_ms).toLong()
        dashboardUrl = getString(R.string.dashboard_url)

        webView = findViewById(R.id.webview)
        swipe   = findViewById(R.id.swipe)
        footer  = findViewById(R.id.footer)

        configureWebView()

        swipe.setColorSchemeColors(Color.parseColor("#58a6ff"))
        swipe.setProgressBackgroundColorSchemeColor(Color.parseColor("#161b22"))
        swipe.setOnRefreshListener {
            webView.reload()
            lastReloadEpoch = System.currentTimeMillis()
        }

        webView.loadUrl(dashboardUrl)
        lastReloadEpoch = System.currentTimeMillis()
        updateFooter()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        webView.settings.apply {
            javaScriptEnabled              = true
            domStorageEnabled              = true
            cacheMode                      = WebSettings.LOAD_DEFAULT
            loadWithOverviewMode           = true
            useWideViewPort                = true
            builtInZoomControls            = true
            displayZoomControls            = false
            mediaPlaybackRequiresUserGesture = false
        }

        // Force the WebView to render in dark mode so the dashboard's
        // dark theme matches system chrome on Android 15.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(webView.settings, true)
        }

        webView.setBackgroundColor(Color.parseColor("#0d1117"))

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                swipe.isRefreshing = false
                lastReloadEpoch = System.currentTimeMillis()
                updateFooter()
            }

            // Keep all navigation inside the WebView (no surprise browser handoffs).
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean = false
        }
    }

    private fun updateFooter() {
        val ts  = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(lastReloadEpoch))
        val mins = intervalMs / 60_000
        footer.text = "↻ refreshed $ts · auto every ${mins}m"
        footer.visibility = View.VISIBLE
    }

    override fun onResume() {
        super.onResume()
        // Schedule next auto-reload `intervalMs` from the LAST reload, not from now.
        val sinceLast = System.currentTimeMillis() - lastReloadEpoch
        val initialDelay = (intervalMs - sinceLast).coerceAtLeast(0L)
        handler.postDelayed(reloadTask, initialDelay)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(reloadTask)
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}
