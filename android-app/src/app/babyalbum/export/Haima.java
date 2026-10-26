package app.babyalbum.export;

import android.graphics.Rect;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Page model of the official Haima app (com.simshine.cn.aiothd), verified on 3.4.2.
 * Mirrors the desktop adapter: same pages, same resource ids, same safety rules.
 */
final class Haima {
    static final String PKG = "com.simshine.cn.aiothd";
    static final String ID = PKG + ":id/";
    static final String EXPORT_RELATIVE_PATH = "DCIM/Camera/海马爸比/";
    static final String VERIFIED_VERSION = "3.4.2";

    /** Words that are never tapped, whatever page we believe we are on. */
    static final String[] FORBIDDEN = {"删除", "清空", "移除", "解绑", "注销", "退出登录", "格式化"};

    /** The only controls navigation may click. */
    static final List<String> NAV = Arrays.asList(
            "enter_device_detail", "rl_picture", "iv_title_right", "allImage",
            "showSwitch", "tv_media_library_edit", "iv_media_library_close", "homeTabLayout");

    enum Page {
        HOME("首页"), HOME_OTHER_TAB("首页的其他标签"), DEVICE("设备页"), ALBUM("时光相册"),
        ALBUM_MENU("时光相册菜单"), MOMENTS("精彩瞬间"), MOMENTS_EDIT("精彩瞬间（编辑中）"),
        OTHER_APP("其他 App"), UNKNOWN("未知页面");

        final String label;

        Page(String label) {
            this.label = label;
        }
    }

    static final class Group {
        final String date;
        final Rect header;
        final AccessibilityNodeInfo box;
        final Rect boxRect;
        final boolean checked;

        Group(String date, Rect header, AccessibilityNodeInfo box, Rect boxRect) {
            this.date = date;
            this.header = header;
            this.box = box;
            this.boxRect = boxRect;
            this.checked = box != null && box.isChecked();
        }
    }

    private Haima() {
    }

    static List<AccessibilityNodeInfo> byId(AccessibilityNodeInfo root, String shortId) {
        List<AccessibilityNodeInfo> out = new ArrayList<>();
        if (root == null) return out;
        List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByViewId(ID + shortId);
        if (found == null) return out;
        for (AccessibilityNodeInfo n : found) {
            if (n != null && n.isVisibleToUser()) out.add(n);
        }
        return out;
    }

    static AccessibilityNodeInfo first(AccessibilityNodeInfo root, String shortId) {
        List<AccessibilityNodeInfo> found = byId(root, shortId);
        return found.isEmpty() ? null : found.get(0);
    }

    static boolean has(AccessibilityNodeInfo root, String shortId) {
        return first(root, shortId) != null;
    }

    static String text(AccessibilityNodeInfo n) {
        CharSequence t = n == null ? null : n.getText();
        return t == null ? "" : t.toString().trim();
    }

    static Rect bounds(AccessibilityNodeInfo n) {
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        return r;
    }

    static Page detect(AccessibilityNodeInfo root) {
        if (root == null) return Page.OTHER_APP;  // no readable Haima window in front
        CharSequence pkg = root.getPackageName();
        if (pkg == null || !PKG.contentEquals(pkg)) return Page.OTHER_APP;
        if (has(root, "tv_media_library_selected") && has(root, "download_text")) return Page.MOMENTS_EDIT;
        if (has(root, "showSwitch") && has(root, "tv_media_library_edit")) return Page.MOMENTS;
        if (has(root, "allImage")) return Page.ALBUM_MENU;
        AccessibilityNodeInfo title = first(root, "tv_title_middle");
        if (title != null && "时光相册".equals(text(title)) && has(root, "iv_title_right")) return Page.ALBUM;
        if (has(root, "rl_picture") && has(root, "main_rb_device")) return Page.DEVICE;
        if (has(root, "enter_device_detail")) return Page.HOME;
        if (has(root, "homeTabLayout") && has(root, "myTabLayout")) return Page.HOME_OTHER_TAB;
        return Page.UNKNOWN;
    }

    /** Date headers, top to bottom, each paired with its select-all checkbox. */
    static List<Group> groups(AccessibilityNodeInfo root) {
        List<AccessibilityNodeInfo> boxes = byId(root, "days_checkbox");
        List<Rect> boxRects = new ArrayList<>();
        for (AccessibilityNodeInfo b : boxes) boxRects.add(bounds(b));
        List<Group> out = new ArrayList<>();
        for (AccessibilityNodeInfo h : byId(root, "timeTV")) {
            Rect hr = bounds(h);
            AccessibilityNodeInfo box = null;
            Rect boxRect = null;
            for (int i = 0; i < boxes.size(); i++) {
                Rect r = boxRects.get(i);
                if (r.centerY() >= hr.top && r.centerY() <= hr.bottom) {
                    box = boxes.get(i);
                    boxRect = r;
                    break;
                }
            }
            out.add(new Group(text(h), hr, box, boxRect));
        }
        out.sort((a, b) -> Integer.compare(a.header.top, b.header.top));
        return out;
    }

    static int selectedCount(AccessibilityNodeInfo root) {
        String t = text(first(root, "tv_media_library_selected"));
        StringBuilder digits = new StringBuilder();
        for (char c : t.toCharArray()) if (Character.isDigit(c)) digits.append(c);
        if (digits.length() == 0) return -1;
        try {
            return Integer.parseInt(digits.toString());
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    /** {top, bottom} of the band where list items can be tapped without touching the bars. */
    static int[] band(AccessibilityNodeInfo root) {
        AccessibilityNodeInfo title = first(root, "constrain_layout_title");
        if (title == null) title = first(root, "tv_media_library_selected");
        AccessibilityNodeInfo bar = first(root, "bottom_edit_area");
        if (bar == null) bar = first(root, "download_text");
        int top = title != null ? bounds(title).bottom : 246;
        int bottom = bar != null ? bounds(bar).top : 2214;
        return new int[]{top, bottom};
    }

    static Boolean filterOn(AccessibilityNodeInfo root) {
        AccessibilityNodeInfo sw = first(root, "showSwitch");
        return sw == null ? null : sw.isChecked();
    }

    static boolean hasText(AccessibilityNodeInfo root, String text) {
        List<AccessibilityNodeInfo> found = root == null ? null : root.findAccessibilityNodeInfosByText(text);
        if (found == null) return false;
        for (AccessibilityNodeInfo n : found) if (text.equals(text(n))) return true;
        return false;
    }

    static boolean isEmpty(AccessibilityNodeInfo root) {
        return detect(root) == Page.MOMENTS && groups(root).isEmpty() && hasText(root, "暂无内容");
    }

    static boolean atTop(AccessibilityNodeInfo root) {
        List<Group> gs = groups(root);
        if (gs.isEmpty()) return true;
        int top;
        if (detect(root) == Page.MOMENTS_EDIT) {
            top = band(root)[0];
        } else {
            AccessibilityNodeInfo grid = first(root, "recyclerView");
            top = grid != null ? bounds(grid).top : 366;
        }
        Rect h = gs.get(0).header;
        return Math.abs(h.top - top) <= 2 && h.height() >= 110;
    }

    static boolean forbidden(AccessibilityNodeInfo n) {
        String label = text(n) + (n.getContentDescription() == null ? "" : n.getContentDescription().toString());
        for (String w : FORBIDDEN) if (label.contains(w)) return true;
        return false;
    }
}
