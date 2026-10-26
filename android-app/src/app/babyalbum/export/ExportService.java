package app.babyalbum.export;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Accessibility service: hosts the export engine, a progress strip and the gesture API. */
public final class ExportService extends AccessibilityService implements Status.Listener {
    static volatile ExportService instance;

    private Handler main;
    private Thread worker;
    private volatile Engine engine;
    private LinearLayout strip;
    private TextView stripText;
    private boolean receiverRegistered;

    private final BroadcastReceiver screenOff = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            stopExport("屏幕已关闭，导出已暂停。重新开始会从断点继续。");
        }
    };

    @Override
    protected void onServiceConnected() {
        instance = this;
        main = new Handler(Looper.getMainLooper());
        IntentFilter f = new IntentFilter(Intent.ACTION_SCREEN_OFF);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(screenOff, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(screenOff, f);
        }
        receiverRegistered = true;
        Status.I.addListener(this);
        Status.I.changed();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // The engine polls the window tree; events are not needed.
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public boolean onUnbind(Intent intent) {
        shutdown();
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        shutdown();
        super.onDestroy();
    }

    private void shutdown() {
        stopExport("无障碍服务已关闭。");
        Status.I.removeListener(this);
        if (receiverRegistered) {
            try {
                unregisterReceiver(screenOff);
            } catch (RuntimeException ignored) {
            }
            receiverRegistered = false;
        }
        hideStrip();
        instance = null;
        Status.I.changed();
    }

    boolean isRunning() {
        return worker != null && worker.isAlive();
    }

    void startExport(int batchTarget) {
        if (isRunning()) return;
        engine = new Engine(this, batchTarget);
        worker = new Thread(engine, "album-export");
        worker.start();
        showStrip();
    }

    void stopExport(String reason) {
        Engine e = engine;
        if (e != null && isRunning()) e.requestStop(reason);
    }

    /** Called on the export thread when the run ends. */
    void onExportFinished() {
        main.post(() -> {
            hideStrip();
            Intent i = new Intent(this, MainActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            try {
                startActivity(i);
            } catch (RuntimeException ignored) {
            }
        });
    }

    /** Dispatch a gesture and wait for it to finish (call from the worker thread). */
    boolean gesture(GestureDescription g, long timeoutMs) {
        CountDownLatch done = new CountDownLatch(1);
        AtomicBoolean ok = new AtomicBoolean(false);
        boolean sent = dispatchGesture(g, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription d) {
                ok.set(true);
                done.countDown();
            }

            @Override
            public void onCancelled(GestureDescription d) {
                done.countDown();
            }
        }, main);
        if (!sent) return false;
        try {
            done.await(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return ok.get();
    }

    // ------------------------------------------------------------- progress strip
    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private void showStrip() {
        main.post(() -> {
            if (strip != null) return;
            strip = new LinearLayout(this);
            strip.setOrientation(LinearLayout.HORIZONTAL);
            strip.setGravity(Gravity.CENTER_VERTICAL);
            strip.setBackgroundColor(Color.argb(230, 33, 33, 33));
            strip.setPadding(dp(12), 0, dp(4), 0);
            strip.setKeepScreenOn(true);
            stripText = new TextView(this);
            stripText.setTextColor(Color.WHITE);
            stripText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            stripText.setSingleLine(true);
            stripText.setText("准备导出……");
            strip.addView(stripText, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            Button stop = new Button(this);
            stop.setText("停止");
            stop.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
            stop.setMinHeight(0);
            stop.setMinimumHeight(0);
            stop.setPadding(dp(10), 0, dp(10), 0);
            stop.setOnClickListener(v -> stopExport("已手动停止。"));
            strip.addView(stop, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, dp(30)));
            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                    WindowManager.LayoutParams.MATCH_PARENT, statusBarHeight(),
                    WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                            | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.TOP;
            lp.y = 0;
            if (Build.VERSION.SDK_INT >= 28) {
                lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            }
            try {
                ((WindowManager) getSystemService(WINDOW_SERVICE)).addView(strip, lp);
            } catch (RuntimeException e) {
                strip = null;
            }
        });
    }

    private int statusBarHeight() {
        int id = getResources().getIdentifier("status_bar_height", "dimen", "android");
        int h = id > 0 ? getResources().getDimensionPixelSize(id) : dp(28);
        return Math.max(h, dp(28));
    }

    private void hideStrip() {
        if (main == null) return;
        main.post(() -> {
            if (strip == null) return;
            try {
                ((WindowManager) getSystemService(WINDOW_SERVICE)).removeView(strip);
            } catch (RuntimeException ignored) {
            }
            strip = null;
            stripText = null;
        });
    }

    @Override
    public void onStatus() {
        if (stripText != null) stripText.setText(Status.I.line());
    }

    static boolean enabledInSettings(Context c) {
        String list = android.provider.Settings.Secure.getString(c.getContentResolver(),
                android.provider.Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        return list != null && list.contains(c.getPackageName() + "/");
    }
}
