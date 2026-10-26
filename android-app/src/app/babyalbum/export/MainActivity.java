package app.babyalbum.export;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** Setup checklist, start/stop, and the result of the last run. */
public final class MainActivity extends Activity implements Status.Listener {
    private TextView serviceState;
    private TextView permState;
    private TextView appState;
    private TextView progress;
    private TextView result;
    private EditText batch;
    private Button serviceBtn;
    private Button permBtn;
    private Button startBtn;
    private Button stopBtn;
    private boolean autostartPending;

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private TextView text(LinearLayout parent, String s, int sp, boolean bold, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(6), 0, dp(6));
        parent.addView(t);
        return t;
    }

    private Button button(LinearLayout parent, String s, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        parent.addView(b);
        return b;
    }

    private LinearLayout row(LinearLayout parent) {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        parent.addView(r);
        return r;
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        ScrollView scroll = new ScrollView(this);
        scroll.setFitsSystemWindows(true);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(20), dp(24), dp(20), dp(32));
        scroll.addView(page);
        setContentView(scroll);

        int ink = Color.rgb(28, 28, 30);
        int quiet = Color.rgb(110, 110, 115);
        text(page, "宝宝相册导出", 24, true, ink);
        text(page, "适用于海马爸比 · 自动在「精彩瞬间」里按日期勾选并下载，照片保存在手机相册的「海马爸比」文件夹。照片不会上传。", 14, false, quiet);

        text(page, "准备", 18, true, ink);
        LinearLayout r1 = row(page);
        serviceState = text(r1, "", 15, false, ink);
        serviceState.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        serviceBtn = button(r1, "去开启", v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        LinearLayout r2 = row(page);
        permState = text(r2, "", 15, false, ink);
        permState.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        permBtn = button(r2, "允许", v -> requestPermissions(new String[]{PhotoStore.permission()}, 1));
        appState = text(page, "", 15, false, ink);

        text(page, "导出", 18, true, ink);
        LinearLayout r3 = row(page);
        TextView label = text(r3, "每批勾选约", 15, false, ink);
        label.setPadding(0, 0, dp(8), 0);
        batch = new EditText(this);
        batch.setInputType(InputType.TYPE_CLASS_NUMBER);
        batch.setText("120");
        batch.setEms(4);
        r3.addView(batch);
        text(r3, " 张", 15, false, ink);
        LinearLayout r4 = row(page);
        startBtn = button(r4, "开始导出", v -> start());
        stopBtn = button(r4, "停止", v -> {
            ExportService s = ExportService.instance;
            if (s != null) s.stopExport("已手动停止。");
        });
        progress = text(page, "", 15, false, ink);
        result = text(page, "", 15, false, ink);

        text(page, "说明", 18, true, ink);
        text(page, "· 导出时会自动打开海马爸比，请保持亮屏、不要操作手机；顶部黑条显示进度，可随时点“停止”。\n"
                + "· 只下载“仅显示未下载的文件”里的照片，已下载的不会重复下载；中断后再次开始会接着下载。\n"
                + "· 从不点击“删除”；遇到不认识的页面会自动暂停。\n"
                + "· 要把照片存到电脑，可以用数据线连接后复制「DCIM/Camera/海马爸比」文件夹，或使用桌面版。", 14, false, quiet);

        autostartPending = getIntent() != null && getIntent().getBooleanExtra("autostart", false);
        if (getIntent() != null && getIntent().hasExtra("batch")) {
            batch.setText(String.valueOf(getIntent().getIntExtra("batch", 120)));
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (intent.getBooleanExtra("autostart", false)) autostartPending = true;
        if (intent.hasExtra("batch")) batch.setText(String.valueOf(intent.getIntExtra("batch", 120)));
    }

    @Override
    protected void onResume() {
        super.onResume();
        Status.I.addListener(this);
        refresh();
        if (autostartPending) {
            autostartPending = false;
            start();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        Status.I.removeListener(this);
    }

    private void start() {
        ExportService s = ExportService.instance;
        if (s == null) {
            Toast.makeText(this, "请先在无障碍设置中开启「宝宝相册导出」", Toast.LENGTH_LONG).show();
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            return;
        }
        if (s.isRunning()) return;
        int n = 120;
        try {
            n = Integer.parseInt(batch.getText().toString().trim());
        } catch (NumberFormatException ignored) {
        }
        s.startExport(Math.max(10, Math.min(n, 500)));
        refresh();
    }

    private void refresh() {
        boolean on = ExportService.instance != null;
        serviceState.setText(on ? "✓ 无障碍服务已开启" : "✗ 无障碍服务未开启（必需）");
        serviceBtn.setVisibility(on ? View.GONE : View.VISIBLE);
        boolean perm = PhotoStore.canRead(this);
        permState.setText(perm ? "✓ 已允许读取相册（用于核对下载数量）" : "○ 未允许读取相册（可选，用于核对下载数量）");
        permBtn.setVisibility(perm ? View.GONE : View.VISIBLE);
        String version = null;
        try {
            PackageInfo info = getPackageManager().getPackageInfo(Haima.PKG, 0);
            version = info.versionName;
        } catch (PackageManager.NameNotFoundException ignored) {
        }
        if (version == null) {
            appState.setText("✗ 没有找到海马爸比 App");
        } else {
            appState.setText("✓ 海马爸比 " + version + (Haima.VERIFIED_VERSION.equals(version) ? "（已验证）" : "（未验证版本，遇到陌生页面会暂停）"));
        }
        Status st = Status.I;
        boolean running = on && ExportService.instance.isRunning();
        startBtn.setEnabled(!running && version != null);
        stopBtn.setEnabled(running);
        if (st.startedAt == 0) {
            progress.setText("");
        } else {
            long s = st.elapsedSeconds();
            progress.setText(String.format("%s\n第 %d 批 · 本次下载 %d 张 · 用时 %d 分 %d 秒%s",
                    running ? st.phase : "上次导出", st.batches, st.downloaded, s / 60, s % 60,
                    st.folderNow >= 0 ? "\n相册「海马爸比」文件夹现有 " + st.folderNow + " 张" : ""));
        }
        if (!running && !st.result.isEmpty()) {
            result.setText(st.resultText() + (st.note.isEmpty() ? "" : "：" + st.note));
            result.setTextColor("done".equals(st.result) ? Color.rgb(30, 130, 76) : Color.rgb(196, 90, 0));
        } else {
            result.setText("");
        }
    }

    @Override
    public void onStatus() {
        refresh();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        refresh();
    }
}
