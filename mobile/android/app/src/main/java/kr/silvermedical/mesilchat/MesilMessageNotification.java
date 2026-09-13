package kr.silvermedical.mesilchat;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import androidx.core.app.NotificationCompat;
import java.util.Map;

final class MesilMessageNotification {
    private static final String CHANNEL_ID = "mesil-messages-v1";

    private MesilMessageNotification() {}

    static void show(Context context, Map<String, String> data) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        createChannel(manager);
        String tag = value(data, "tag", "mesil-chat-message");
        String relativeUrl = value(data, "url", "/");
        Uri base = Uri.parse(BuildConfig.MESIL_SERVER_URL);
        Uri relative = Uri.parse(relativeUrl.startsWith("/") ? relativeUrl : "/");
        Uri target = base.buildUpon()
            .encodedPath(relative.getEncodedPath())
            .encodedQuery(relative.getEncodedQuery())
            .build();
        Intent intent = new Intent(context, MainActivity.class)
            .setData(target)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent open = PendingIntent.getActivity(
            context,
            tag.hashCode() & 0x7fffffff,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mesil_call)
            .setColor(Color.rgb(82, 127, 20))
            .setContentTitle(value(data, "title", "MESIL_Chat"))
            .setContentText(value(data, "body", "새 메시지가 도착했습니다."))
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setAutoCancel(true)
            .setContentIntent(open);
        manager.notify(tag, tag.hashCode() & 0x7fffffff, builder.build());
    }

    private static void createChannel(NotificationManager manager) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || manager.getNotificationChannel(CHANNEL_ID) != null) return;
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID,
            "MESIL_Chat 메시지",
            NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("새 업무대화와 답글 알림");
        channel.setLockscreenVisibility(Notification.VISIBILITY_PRIVATE);
        channel.enableVibration(true);
        AudioAttributes audio = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build();
        channel.setSound(Settings.System.DEFAULT_NOTIFICATION_URI, audio);
        manager.createNotificationChannel(channel);
    }

    private static String value(Map<String, String> data, String key, String fallback) {
        String value = data.get(key);
        return value == null || value.isBlank() ? fallback : value;
    }
}
