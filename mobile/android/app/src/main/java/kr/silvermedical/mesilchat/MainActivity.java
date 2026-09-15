package kr.silvermedical.mesilchat;

import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.view.WindowManager;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    static final String EXTRA_SHOW_CALL_OVER_LOCK_SCREEN = "mesil_show_call_over_lock_screen";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        prepareCallWindow(getIntent());
        registerPlugin(MesilShareReceiverPlugin.class);
        registerPlugin(MesilPushPlugin.class);
        super.onCreate(savedInstanceState);
        getBridge().getWebView().addJavascriptInterface(new OfflineRetryBridge(), "MesilOffline");
        openMesilIntent(getIntent());
    }

    @Override
    public void onStart() {
        super.onStart();
        MesilAppVisibility.setForeground(true);
    }

    @Override
    public void onStop() {
        MesilAppVisibility.setForeground(false);
        super.onStop();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        prepareCallWindow(intent);
        openMesilIntent(intent);
    }

    void setCallScreenActive(boolean active) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(active);
            setTurnScreenOn(active);
        }
        if (active) {
            getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED |
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON |
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            );
        } else {
            getWindow().clearFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED |
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON |
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            );
        }
    }

    private void prepareCallWindow(Intent intent) {
        if (intent != null && intent.getBooleanExtra(EXTRA_SHOW_CALL_OVER_LOCK_SCREEN, false)) {
            setCallScreenActive(true);
        }
    }

    private void openMesilIntent(Intent intent) {
        if (intent == null || intent.getData() == null || getBridge() == null) return;
        Uri uri = intent.getData();
        if (!matchesConfiguredServer(uri)) return;
        String callId = uri.getQueryParameter("call");
        if (callId != null && !callId.isBlank()) {
            MesilCallNotification.cancel(this, callId);
        }
        MesilPushPlugin.storePendingCallAction(this, uri);
        getBridge().getWebView().post(() -> getBridge().getWebView().loadUrl(uri.toString()));
    }

    private boolean matchesConfiguredServer(Uri uri) {
        Uri configured = Uri.parse(BuildConfig.MESIL_SERVER_URL);
        return safe(configured.getScheme()).equals(uri.getScheme())
            && safe(configured.getHost()).equals(uri.getHost())
            && normalizedPort(configured) == normalizedPort(uri);
    }

    private int normalizedPort(Uri uri) {
        int port = uri.getPort();
        if (port >= 0) return port;
        return "https".equals(uri.getScheme()) ? 443 : 80;
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }

    private final class OfflineRetryBridge {
        @JavascriptInterface
        public void retry() {
            if (getBridge() == null || getBridge().getWebView() == null) return;
            getBridge().getWebView().post(
                () -> getBridge().getWebView().loadUrl(BuildConfig.MESIL_SERVER_URL)
            );
        }
    }
}
