package app.babyalbum.export;

import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.SystemClock;
import android.util.Log;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The export state machine. Runs on its own thread; every action is either a
 * click on a whitelisted control or a tap on a verified date checkbox.
 */
final class Engine implements Runnable {
    static final String TAG = "AlbumExport";

    static final class NeedsUser extends Exception {
        NeedsUser(String m) {
            super(m);
        }
    }

    static final class Unsafe extends Exception {
        Unsafe(String m) {
            super(m);
        }
    }

    static final class Stopped extends Exception {
        Stopped(String m) {
            super(m);
        }
    }

    private static final class Selection {
        int count;
        List<String> dates = new ArrayList<>();
    }

    private final ExportService svc;
    private final int target;
    private final Status st = Status.I;
    private volatile String stopReason;

    Engine(ExportService svc, int target) {
        this.svc = svc;
        this.target = Math.max(10, target);
    }

    void requestStop(String reason) {
        stopReason = reason;
    }

    @Override
    public void run() {
        st.begin(PhotoStore.count(svc));
        String result = "done";
        String note = "";
        try {
            launchHaima();
            AccessibilityNodeInfo root = navigateToMoments();
            root = ensureFilterOn(root);
            loop(root);
        } catch (NeedsUser e) {
            result = "paused";
            note = e.getMessage();
        } catch (Unsafe e) {
            result = "safety";
            note = "安全检查拦截了一次操作：" + e.getMessage();
        } catch (Stopped e) {
            result = "stopped";
            note = e.getMessage();
        } catch (Exception e) {
            result = "error";
            note = String.valueOf(e);
            Log.e(TAG, "export failed", e);
        } finally {
            tryLeaveEdit();
            st.finish(result, note, PhotoStore.count(svc));
            Log.i(TAG, "finished: " + result + " " + note + " batches=" + st.batches + " downloaded=" + st.downloaded
                    + " seconds=" + st.elapsedSeconds());
            svc.onExportFinished();
        }
    }

    // ---------------------------------------------------------------- basics
    private void checkStop() throws Stopped {
        String r = stopReason;
        if (r != null) throw new Stopped(r);
    }

    private void sleep(long ms) throws Stopped {
        long end = SystemClock.uptimeMillis() + ms;
        while (true) {
            checkStop();
            long left = end - SystemClock.uptimeMillis();
            if (left <= 0) return;
            SystemClock.sleep(Math.min(left, 100));
        }
    }

    /**
     * Root of the focused application window. getRootInActiveWindow() is not used for this:
     * our event filter only covers the official app, so the service's idea of the "active"
     * window can stay on a Haima window that is already behind another screen or closed.
     */
    private AccessibilityNodeInfo topRoot() {
        try {
            AccessibilityWindowInfo firstApp = null;
            for (AccessibilityWindowInfo w : svc.getWindows()) {
                if (w.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
                if (w.isFocused()) return w.getRoot();
                if (firstApp == null) firstApp = w;
            }
            if (firstApp != null) return firstApp.getRoot();
        } catch (RuntimeException ignored) {
        }
        return null;
    }

    private AccessibilityNodeInfo root() throws Stopped {
        for (int i = 0; i < 12; i++) {
            checkStop();
            AccessibilityNodeInfo r = topRoot();
            if (r != null) return r;
            sleep(150);
        }
        return null;
    }

    private void log(String msg) {
        Log.i(TAG, msg);
    }

    // ---------------------------------------------------------------- input
    private boolean tap(int x, int y) {
        Path p = new Path();
        p.moveTo(x, y);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, 60)).build();
        return svc.gesture(g, 3000);
    }

    /** Scroll the list one page with no fling: move, hold still, release. */
    private void dragUp(int[] band) throws Stopped {
        int x = 300;
        int y1 = band[1] - 120;
        int y2 = band[0] + 160;
        Path move = new Path();
        move.moveTo(x, y1);
        move.lineTo(x, y2);
        GestureDescription.StrokeDescription s1 = new GestureDescription.StrokeDescription(move, 0, 450, true);
        if (svc.gesture(new GestureDescription.Builder().addStroke(s1).build(), 3000)) {
            Path hold = new Path();
            hold.moveTo(x, y2);
            hold.lineTo(x, y2 + 1);
            GestureDescription.StrokeDescription s2 = s1.continueStroke(hold, 0, 250, false);
            svc.gesture(new GestureDescription.Builder().addStroke(s2).build(), 3000);
        }
        sleep(350);
    }

    /** Fast downward flings to return to the newest groups. */
    private void flingToTop(int[] band) throws Stopped {
        for (int i = 0; i < 4; i++) {
            Path p = new Path();
            p.moveTo(300, band[0] + 150);
            p.lineTo(300, band[1] - 150);
            svc.gesture(new GestureDescription.Builder()
                    .addStroke(new GestureDescription.StrokeDescription(p, 0, 60)).build(), 2000);
            sleep(120);
        }
        sleep(700);
    }

    private void checkNode(AccessibilityNodeInfo root, AccessibilityNodeInfo n) throws Unsafe {
        CharSequence pkg = n.getPackageName();
        if (pkg != null && !Haima.PKG.contentEquals(pkg)) throw new Unsafe("目标控件不属于海马 App");
        if (Haima.forbidden(n)) throw new Unsafe("拒绝点击危险控件：" + Haima.text(n));
        Rect r = Haima.bounds(n);
        for (AccessibilityNodeInfo danger : Haima.byId(root, "delete_text")) {
            if (Haima.bounds(danger).contains(r.centerX(), r.centerY())) throw new Unsafe("目标落在“删除”按钮范围内");
        }
    }

    /** Click a whitelisted control. A control that is momentarily absent (the page is reloading) is not unsafe: return false and let the caller re-read. */
    private boolean clickNav(AccessibilityNodeInfo root, String id, long settle) throws Unsafe, Stopped {
        if (!Haima.NAV.contains(id)) throw new Unsafe("不在导航白名单内：" + id);
        AccessibilityNodeInfo n = Haima.first(root, id);
        if (n == null) {
            log("control not present yet: " + id);
            sleep(Math.min(settle, 800));
            return false;
        }
        checkNode(root, n);
        boolean ok = n.isClickable() && n.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        if (!ok) {
            Rect r = Haima.bounds(n);
            tap(r.centerX(), r.centerY());
        }
        sleep(settle);
        return true;
    }

    private void tapGroup(AccessibilityNodeInfo root, Haima.Group g, int[] band) throws Unsafe {
        if (g.box == null) throw new Unsafe("日期勾选框缺失：" + g.date);
        if (g.boxRect.top < band[0] + 4 || g.boxRect.bottom > band[1] - 4) throw new Unsafe("勾选框被遮挡：" + g.date);
        checkNode(root, g.box);
        tap(g.boxRect.centerX(), g.boxRect.centerY());
    }

    private void clickDownload(AccessibilityNodeInfo root) throws Unsafe {
        AccessibilityNodeInfo dl = Haima.first(root, "download_text");
        AccessibilityNodeInfo del = Haima.first(root, "delete_text");
        if (dl == null || !"下载".equals(Haima.text(dl))) throw new Unsafe("没有找到“下载”按钮");
        if (del != null && !"删除".equals(Haima.text(del))) throw new Unsafe("底部按钮布局与已验证版本不同");
        if (dl.isClickable() && dl.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return;
        Rect r = Haima.bounds(dl);
        int x = r.right - r.width() / 4;
        int y = r.centerY();
        if (del != null) {
            Rect d = Haima.bounds(del);
            if (d.contains(x, y) || Math.abs(x - d.centerX()) < 200) throw new Unsafe("下载点击位置离“删除”太近");
        }
        tap(x, y);
    }

    // ---------------------------------------------------------------- navigation
    private boolean haimaInFront() {
        AccessibilityNodeInfo r = topRoot();
        return r != null && r.getPackageName() != null && Haima.PKG.contentEquals(r.getPackageName());
    }

    /**
     * Bring the official app to the front. Some phones (vivo, OPPO, Xiaomi) first ask
     * "允许打开海马爸比吗？"; that is the person's decision, so we only explain and wait.
     */
    private void openHaima() throws NeedsUser, Stopped {
        Intent i = svc.getPackageManager().getLaunchIntentForPackage(Haima.PKG);
        if (i == null) throw new NeedsUser("没有找到海马爸比 App。");
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        svc.startActivity(i);
        long start = SystemClock.uptimeMillis();
        boolean hinted = false;
        while (SystemClock.uptimeMillis() - start < 45000) {
            sleep(500);
            if (haimaInFront()) {
                sleep(hinted ? 3000 : 2500);  // let a cold start get past its splash screen
                return;
            }
            if (!hinted && SystemClock.uptimeMillis() - start > 2500) {
                hinted = true;
                st.phase("请在弹窗中点“始终打开”海马爸比");
            }
        }
        throw new NeedsUser("没有打开海马爸比。如果手机询问是否允许打开，请点“始终打开”后重新开始。");
    }

    private void launchHaima() throws NeedsUser, Stopped {
        openHaima();  // a run always starts from our own screen; bring the official app forward
    }

    private AccessibilityNodeInfo navigateToMoments() throws NeedsUser, Unsafe, Stopped {
        int backs = 0;
        int relaunches = 0;
        int unknownWaits = 0;
        for (int step = 0; step < 18; step++) {
            AccessibilityNodeInfo r = root();
            Haima.Page page = Haima.detect(r);
            st.phase("打开相册：" + page.label);
            log("page " + page);
            switch (page) {
                case MOMENTS:
                    return r;
                case MOMENTS_EDIT:
                    clickNav(r, "iv_media_library_close", 1000);
                    break;
                case ALBUM_MENU:
                    clickNav(r, "allImage", 2500);
                    break;
                case ALBUM:
                    clickNav(r, "iv_title_right", 1000);
                    break;
                case DEVICE:
                    clickNav(r, "rl_picture", 2500);
                    break;
                case HOME:
                    clickNav(r, "enter_device_detail", 3000);
                    break;
                case HOME_OTHER_TAB:
                    clickNav(r, "homeTabLayout", 1000);
                    break;
                case OTHER_APP:
                    if (haimaInFront()) {  // our event filter can hide the tree for a moment
                        sleep(800);
                        break;
                    }
                    if (relaunches >= 2) throw new NeedsUser("海马爸比 App 没有停留在前台。");
                    relaunches++;
                    openHaima();
                    break;
                default:
                    if (unknownWaits < 5) {
                        unknownWaits++;  // loading dialogs and toasts usually clear by themselves
                        sleep(1200);
                        break;
                    }
                    if (backs >= 3) throw new NeedsUser("海马爸比停在一个不认识的页面，请手动回到首页后重试。");
                    svc.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK);
                    backs++;
                    unknownWaits = 0;
                    sleep(1500);
            }
        }
        throw new NeedsUser("没能自动进入「精彩瞬间」，请手动打开：时光相册 → 右上角 → 全部照片。");
    }

    private AccessibilityNodeInfo ensureFilterOn(AccessibilityNodeInfo r) throws NeedsUser, Unsafe, Stopped {
        for (int i = 0; i < 3; i++) {
            Boolean on = Haima.filterOn(r);
            if (on == null) throw new NeedsUser("没有找到“仅显示未下载的文件”开关。");
            if (on) return r;
            clickNav(r, "showSwitch", 2000);
            r = root();
        }
        throw new NeedsUser("无法打开“仅显示未下载的文件”。");
    }

    private AccessibilityNodeInfo enterEdit(AccessibilityNodeInfo r) throws NeedsUser, Unsafe, Stopped {
        for (int i = 0; i < 6; i++) {
            if (Haima.detect(r) == Haima.Page.MOMENTS_EDIT) return r;
            clickNav(r, "tv_media_library_edit", 1200);
            r = root();
        }
        throw new NeedsUser("无法进入编辑模式。");
    }

    private void tryLeaveEdit() {
        try {
            AccessibilityNodeInfo r = topRoot();
            if (Haima.detect(r) == Haima.Page.MOMENTS_EDIT) {
                AccessibilityNodeInfo close = Haima.first(r, "iv_media_library_close");
                if (close != null) close.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            }
        } catch (RuntimeException ignored) {
        }
    }

    // ---------------------------------------------------------------- selection
    private List<Haima.Group> tappable(AccessibilityNodeInfo r, Map<String, Integer> attempts) {
        int[] band = Haima.band(r);
        List<Haima.Group> out = new ArrayList<>();
        for (Haima.Group g : Haima.groups(r)) {
            if (g.box == null || g.checked) continue;
            if (g.boxRect.top < band[0] + 4 || g.boxRect.bottom > band[1] - 4) continue;
            Integer n = attempts.get(g.date);
            if (n != null && n >= 2) continue;
            out.add(g);
        }
        return out;
    }

    private AccessibilityNodeInfo scrollToTop(AccessibilityNodeInfo r) throws NeedsUser, Stopped {
        for (int i = 0; i < 15; i++) {
            if (Haima.atTop(r)) return r;
            flingToTop(Haima.band(r));
            r = root();
        }
        throw new NeedsUser("无法回到列表顶部。");
    }

    private Selection selectBatch(AccessibilityNodeInfo r) throws NeedsUser, Unsafe, Stopped {
        Map<String, Integer> attempts = new LinkedHashMap<>();
        Map<String, Boolean> state = new LinkedHashMap<>();
        r = scrollToTop(r);
        for (int round = 0; round < 120; round++) {
            if (Haima.detect(r) != Haima.Page.MOMENTS_EDIT) throw new NeedsUser("选择照片时离开了编辑模式。");
            for (Haima.Group g : Haima.groups(r)) if (g.box != null) state.put(g.date, g.checked);
            int count = Haima.selectedCount(r);
            if (count >= target) break;
            int[] band = Haima.band(r);
            List<Haima.Group> pending = tappable(r, attempts);
            for (Haima.Group g : pending) {
                tapGroup(r, g, band);
                Integer n = attempts.get(g.date);
                attempts.put(g.date, n == null ? 1 : n + 1);
                state.put(g.date, true);
                sleep(150);
            }
            if (!pending.isEmpty()) {
                sleep(250);
                r = root();
                // Reading the tree is cheap here: confirm every tap before scrolling.
                if (!tappable(r, attempts).isEmpty()) continue;
                if (Haima.selectedCount(r) >= target) break;
            }
            List<Haima.Group> before = Haima.groups(r);
            String markerDate = before.isEmpty() ? null : before.get(before.size() - 1).date;
            int markerTop = before.isEmpty() ? 0 : before.get(before.size() - 1).header.top;
            dragUp(band);
            r = root();
            List<Haima.Group> after = Haima.groups(r);
            boolean moved = after.isEmpty() || markerDate == null
                    || !markerDate.equals(after.get(after.size() - 1).date)
                    || markerTop != after.get(after.size() - 1).header.top;
            if (!moved && tappable(r, attempts).isEmpty()) break;
        }
        for (Haima.Group g : Haima.groups(r)) if (g.box != null) state.put(g.date, g.checked);
        Selection s = new Selection();
        s.count = Haima.selectedCount(r);
        for (Map.Entry<String, Boolean> e : state.entrySet()) if (e.getValue()) s.dates.add(e.getKey());
        return s;
    }

    // ---------------------------------------------------------------- download
    private int waitDownload(int before, int expected) throws Stopped {
        long start = SystemClock.uptimeMillis();
        long deadline = start + 60000L + expected * 4000L;
        if (before < 0) {
            // No photo permission: the app leaves edit mode when the download completes.
            while (SystemClock.uptimeMillis() < deadline) {
                sleep(500);
                if (Haima.detect(topRoot()) != Haima.Page.MOMENTS_EDIT) return expected;
            }
            return 0;
        }
        int last = before;
        long lastChange = start;
        while (true) {
            sleep(500);
            int now = PhotoStore.count(svc);
            long t = SystemClock.uptimeMillis();
            if (now != last) {
                last = now;
                lastChange = t;
                st.folderNow = now;
                st.phase("下载中 " + Math.max(0, now - before) + "/" + expected);
            }
            if (now >= before + expected) break;
            if (t - lastChange > 45000) {
                log("download stalled");
                break;
            }
            if (t > deadline) {
                log("download timeout");
                break;
            }
        }
        return Math.max(0, last - before);
    }

    private AccessibilityNodeInfo awaitRefresh(List<String> dates) throws NeedsUser, Unsafe, Stopped {
        Set<String> picked = new HashSet<>(dates);
        long deadline = SystemClock.uptimeMillis() + 15000;
        boolean forced = false;
        int transient_ = 0;
        while (true) {
            AccessibilityNodeInfo r = root();
            Haima.Page page = Haima.detect(r);
            if (page == Haima.Page.UNKNOWN && transient_ < 30) {
                // The app shows a short loading dialog while saving; wait it out, never press Back here.
                transient_++;
                sleep(500);
                continue;
            }
            if (page == Haima.Page.MOMENTS_EDIT) {
                if (SystemClock.uptimeMillis() > deadline) {
                    clickNav(r, "iv_media_library_close", 1200);
                } else {
                    sleep(800);
                }
                continue;
            }
            if (page != Haima.Page.MOMENTS) return ensureFilterOn(navigateToMoments());
            List<Haima.Group> visible = Haima.groups(r);
            boolean loaded = !visible.isEmpty() || Haima.hasText(r, "暂无内容");
            boolean stale = false;
            for (Haima.Group g : visible) if (picked.contains(g.date)) stale = true;
            if (loaded && !stale) return r;
            if (!loaded && SystemClock.uptimeMillis() <= deadline) {
                sleep(500);  // the list is reloading after the download
                continue;
            }
            if (SystemClock.uptimeMillis() > deadline) {
                if (forced) return r;
                log("list not refreshed; toggling the filter");
                clickNav(r, "showSwitch", 1500);
                clickNav(root(), "showSwitch", 2000);
                forced = true;
                deadline = SystemClock.uptimeMillis() + 6000;
                continue;
            }
            sleep(600);
        }
    }

    // ---------------------------------------------------------------- loop
    private void loop(AccessibilityNodeInfo r) throws NeedsUser, Unsafe, Stopped {
        int noProgress = 0;
        while (true) {
            checkStop();
            if (Haima.isEmpty(r)) {
                st.phase("全部完成");
                log("queue empty");
                return;
            }
            st.phase("勾选照片");
            long t0 = SystemClock.uptimeMillis();
            r = enterEdit(r);
            Selection sel = selectBatch(r);
            long t1 = SystemClock.uptimeMillis();
            log("batch " + (st.batches + 1) + " selected " + sel.count + " in " + sel.dates.size() + " groups "
                    + (sel.dates.isEmpty() ? "" : sel.dates.get(0) + ".." + sel.dates.get(sel.dates.size() - 1))
                    + " select_ms=" + (t1 - t0));
            if (sel.count <= 0) throw new NeedsUser("没能选中任何照片，请确认停在「精彩瞬间」页面。");
            int before = PhotoStore.count(svc);
            st.phase("下载中");
            clickDownload(root());
            int got = waitDownload(before, sel.count);
            long t2 = SystemClock.uptimeMillis();
            st.batches += 1;
            st.selected += sel.count;
            st.downloaded += got;
            if (!sel.dates.isEmpty()) st.lastDate = sel.dates.get(sel.dates.size() - 1);
            st.folderNow = PhotoStore.count(svc);
            st.changed();
            log("batch " + st.batches + " downloaded " + got + "/" + sel.count + " download_ms=" + (t2 - t1));
            if (got == 0) {
                noProgress++;
                if (noProgress >= 2) throw new NeedsUser("连续两批没有下载到照片，请检查网络后重试。");
            } else {
                noProgress = 0;
            }
            r = awaitRefresh(sel.dates);
        }
    }
}
