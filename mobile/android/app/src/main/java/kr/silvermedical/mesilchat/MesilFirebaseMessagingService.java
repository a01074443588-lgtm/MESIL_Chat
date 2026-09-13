package kr.silvermedical.mesilchat;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import java.util.Map;

public class MesilFirebaseMessagingService extends FirebaseMessagingService {
    @Override
    public void onNewToken(String token) {
        super.onNewToken(token);
        getSharedPreferences(MesilPushPlugin.PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(MesilPushPlugin.LAST_TOKEN, token)
            .putBoolean(MesilPushTokenSyncWorker.TOKEN_SYNC_PENDING, true)
            .apply();
        MesilPushTokenSyncWorker.enqueue(this);
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        Map<String, String> data = message.getData();
        String kind = data.get("kind");
        if ("message".equals(kind) || "comment".equals(kind)) {
            if (!MesilAppVisibility.isForeground()) {
                MesilMessageNotification.show(this, data);
            }
            return;
        }
        String callId = data.get("call_id");
        if (callId == null || callId.isBlank()) return;

        MesilCallDeliveryReporter.reportAsync(this, data, "device_received", true);

        if ("voice_call_cancel".equals(kind)) {
            MesilCallNotification.cancel(this, callId);
            MesilCallDeliveryReporter.reportAsync(this, data, "notification_cancelled", true);
            return;
        }
        if (!"voice_call".equals(kind)) return;
        long expiresAt;
        try {
            expiresAt = Long.parseLong(data.get("expires_at"));
        } catch (NumberFormatException exception) {
            return;
        }
        Context appContext = getApplicationContext();
        long graceMillis = MesilCallDeliveryState.webClaimGraceMillis(
            MesilAppVisibility.isForeground()
        );
        Runnable showIfUnclaimed = () -> {
            if (!MesilCallDeliveryState.claimIncoming(appContext, callId, expiresAt)) return;
            boolean posted = MesilCallNotification.show(appContext, data);
            MesilCallDeliveryReporter.reportAsync(
                appContext, data, "notification_posted", posted
            );
            if (posted) {
                // Android owns playback; only the ringtone request is observable here.
                MesilCallDeliveryReporter.reportAsync(
                    appContext, data, "ringtone_requested", true
                );
            }
        };
        if (graceMillis > 0L) {
            new Handler(Looper.getMainLooper()).postDelayed(showIfUnclaimed, graceMillis);
        } else {
            showIfUnclaimed.run();
        }
    }
}
