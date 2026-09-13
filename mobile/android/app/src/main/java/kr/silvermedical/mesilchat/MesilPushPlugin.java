package kr.silvermedical.mesilchat;

import android.Manifest;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
import java.util.UUID;

@CapacitorPlugin(
    name = "MesilPush",
    permissions = {
        @Permission(alias = "notifications", strings = { Manifest.permission.POST_NOTIFICATIONS }),
        @Permission(alias = "microphone", strings = { Manifest.permission.RECORD_AUDIO }),
        @Permission(alias = "camera", strings = { Manifest.permission.CAMERA })
    }
)
public class MesilPushPlugin extends Plugin {
    static final String PREFERENCES = "mesil-push";
    static final String INSTALLATION_ID = "installation-id";
    static final String LAST_TOKEN = "last-token";
    static final String PENDING_CALL_ID = "pending-call-id";
    static final String PENDING_CALL_ACTION = "pending-call-action";
    static final String PENDING_CALL_EXPIRES_AT = "pending-call-expires-at";

    static void storePendingCallAction(Context context, Uri uri) {
        if (uri == null || !"accept".equals(uri.getQueryParameter("call_action"))) return;
        String callId = uri.getQueryParameter("call");
        if (callId == null || callId.isBlank()) return;
        long expiresAt;
        try {
            expiresAt = Long.parseLong(uri.getQueryParameter("call_expires"));
        } catch (NumberFormatException exception) {
            expiresAt = System.currentTimeMillis() + 50_000L;
        }
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putString(PENDING_CALL_ID, callId)
            .putString(PENDING_CALL_ACTION, "accept")
            .putLong(PENDING_CALL_EXPIRES_AT, expiresAt)
            .commit();
    }

    static JSObject readPendingCallAction(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        String callId = preferences.getString(PENDING_CALL_ID, "");
        String action = preferences.getString(PENDING_CALL_ACTION, "");
        long expiresAt = preferences.getLong(PENDING_CALL_EXPIRES_AT, 0L);
        if (callId == null || callId.isBlank() || !"accept".equals(action)) {
            return new JSObject();
        }
        if (expiresAt <= System.currentTimeMillis()) {
            clearPendingCallAction(context, callId);
            return new JSObject();
        }
        JSObject result = new JSObject();
        result.put("callId", callId);
        result.put("action", action);
        result.put("expiresAt", expiresAt);
        return result;
    }

    static void clearPendingCallAction(Context context, String callId) {
        SharedPreferences preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        String storedCallId = preferences.getString(PENDING_CALL_ID, "");
        if (callId != null && !callId.isBlank() && !callId.equals(storedCallId)) return;
        preferences.edit()
            .remove(PENDING_CALL_ID)
            .remove(PENDING_CALL_ACTION)
            .remove(PENDING_CALL_EXPIRES_AT)
            .apply();
    }

    @PluginMethod
    public void register(PluginCall call) {
        boolean notificationMissing = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            getPermissionState("notifications") != PermissionState.GRANTED;
        if (notificationMissing) {
            requestPermissionForAliases(
                new String[] {"notifications"},
                call,
                "callPermissionsResult"
            );
            return;
        }
        completeRegistration(call);
    }

    @PermissionCallback
    private void callPermissionsResult(PluginCall call) {
        if (getPermissionState("notifications") != PermissionState.GRANTED) {
            call.reject("휴대전화 알림 권한을 허용해 주세요.");
            return;
        }
        completeRegistration(call);
    }

    private void completeRegistration(PluginCall call) {
        if (FirebaseApp.getApps(getContext()).isEmpty()) {
            call.reject("Android 푸시 등록파일이 없습니다.");
            return;
        }
        FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
            if (!task.isSuccessful() || task.getResult() == null || task.getResult().isBlank()) {
                call.reject("Android 푸시 주소를 만들지 못했습니다.");
                return;
            }
            SharedPreferences preferences = getContext().getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
            String installationId = preferences.getString(INSTALLATION_ID, null);
            if (installationId == null || installationId.isBlank()) {
                installationId = UUID.randomUUID().toString();
            }
            String token = task.getResult();
            preferences.edit()
                .putString(INSTALLATION_ID, installationId)
                .putString(LAST_TOKEN, token)
                .putBoolean(MesilPushTokenSyncWorker.TOKEN_SYNC_PENDING, true)
                .apply();

            JSObject result = new JSObject();
            result.put("installationId", installationId);
            result.put("token", token);
            result.put("appVersion", getAppVersion());
            result.put("fullScreenIntentAllowed", canUseFullScreenIntent());
            result.put("microphoneAllowed", getPermissionState("microphone") == PermissionState.GRANTED);
            result.put("cameraAllowed", getPermissionState("camera") == PermissionState.GRANTED);
            call.resolve(result);
        });
    }

    @PluginMethod
    public void getPermissionStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("notificationPermission", notificationPermissionValue());
        result.put("fullScreenIntentAllowed", canUseFullScreenIntent());
        call.resolve(result);
    }

    @PluginMethod
    public void getPendingCallAction(PluginCall call) {
        call.resolve(readPendingCallAction(getContext()));
    }

    @PluginMethod
    public void clearPendingCallAction(PluginCall call) {
        clearPendingCallAction(getContext(), call.getString("callId"));
        call.resolve();
    }

    @PluginMethod
    public void acknowledgeWebCall(PluginCall call) {
        String callId = call.getString("callId");
        if (callId == null || callId.isBlank()) {
            call.reject("통화 정보를 확인할 수 없습니다.");
            return;
        }
        if (!MesilCallDeliveryState.shouldAcceptWebClaim(MesilAppVisibility.isForeground())) {
            call.resolve();
            return;
        }
        MesilCallDeliveryState.markWebHandled(getContext(), callId);
        call.resolve();
    }

    @PluginMethod
    public void markRegistrationSynchronized(PluginCall call) {
        MesilPushTokenSyncWorker.markSynchronized(getContext());
        call.resolve();
    }

    @PluginMethod
    public void openNotificationSettings(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            Intent intent = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getContext().getPackageName());
            try {
                getActivity().startActivity(intent);
                call.resolve();
            } catch (Exception firstException) {
                Intent fallback = new Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getContext().getPackageName())
                );
                try {
                    getActivity().startActivity(fallback);
                    call.resolve();
                } catch (Exception fallbackException) {
                    call.reject("휴대전화 알림 설정을 열지 못했습니다.", fallbackException);
                }
            }
        });
    }

    @PluginMethod
    public void openFullScreenIntentSettings(PluginCall call) {
        if (canUseFullScreenIntent()) {
            call.resolve();
            return;
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            call.resolve();
            return;
        }
        getActivity().runOnUiThread(() -> {
            Toast.makeText(
                getContext(),
                "화면이 꺼져도 전화가 오도록 '전체 화면 알림 허용'을 켜 주세요.",
                Toast.LENGTH_LONG
            ).show();
            Intent intent = new Intent(
                Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                Uri.parse("package:" + getContext().getPackageName())
            );
            try {
                getActivity().startActivity(intent);
                call.resolve();
            } catch (Exception exception) {
                call.reject("전체 화면 전화 알림 설정을 열지 못했습니다.", exception);
            }
        });
    }

    @PluginMethod
    public void setCallScreenActive(PluginCall call) {
        boolean active = Boolean.TRUE.equals(call.getBoolean("active"));
        getActivity().runOnUiThread(() -> {
            if (getActivity() instanceof MainActivity) {
                ((MainActivity) getActivity()).setCallScreenActive(active);
            }
            call.resolve();
        });
    }

    @PluginMethod
    public void setCallAudioRoute(PluginCall call) {
        String route = call.getString("route");
        if (route == null || !route.matches("speaker|earpiece|system")) {
            call.reject("통화 소리 출력 방식이 올바르지 않습니다.");
            return;
        }
        getActivity().runOnUiThread(() -> {
            AudioManager audioManager = getContext().getSystemService(AudioManager.class);
            if (audioManager == null) {
                call.reject("휴대전화 통화 소리 장치를 찾지 못했습니다.");
                return;
            }
            boolean applied = true;
            if ("system".equals(route)) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    audioManager.clearCommunicationDevice();
                } else {
                    audioManager.setSpeakerphoneOn(false);
                }
                audioManager.setMode(AudioManager.MODE_NORMAL);
            } else {
                audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    int requestedType = "speaker".equals(route)
                        ? AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
                        : AudioDeviceInfo.TYPE_BUILTIN_EARPIECE;
                    AudioDeviceInfo requestedDevice = null;
                    for (AudioDeviceInfo device : audioManager.getAvailableCommunicationDevices()) {
                        if (device.getType() == requestedType) {
                            requestedDevice = device;
                            break;
                        }
                    }
                    applied = requestedDevice != null && audioManager.setCommunicationDevice(requestedDevice);
                } else {
                    audioManager.setSpeakerphoneOn("speaker".equals(route));
                }
            }
            JSObject result = new JSObject();
            result.put("route", route);
            result.put("applied", applied);
            call.resolve(result);
        });
    }

    private boolean canUseFullScreenIntent() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return true;
        NotificationManager manager = getContext().getSystemService(NotificationManager.class);
        return manager != null && manager.canUseFullScreenIntent();
    }

    private String notificationPermissionValue() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return "granted";
        PermissionState state = getPermissionState("notifications");
        if (state == PermissionState.GRANTED) return "granted";
        if (state == PermissionState.DENIED) return "denied";
        return "prompt";
    }

    private String getAppVersion() {
        try {
            return getContext().getPackageManager()
                .getPackageInfo(getContext().getPackageName(), 0)
                .versionName;
        } catch (PackageManager.NameNotFoundException exception) {
            return "unknown";
        }
    }
}
