package app.babyalbum.export;

import android.os.Handler;
import android.os.Looper;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/** Progress shared between the export thread, the overlay and the main screen. */
final class Status {
    interface Listener {
        void onStatus();
    }

    static final Status I = new Status();

    private final Handler main = new Handler(Looper.getMainLooper());
    private final List<Listener> listeners = new CopyOnWriteArrayList<>();

    volatile boolean running;
    volatile String phase = "";
    volatile String result = "";
    volatile String note = "";
    volatile int batches;
    volatile int selected;
    volatile int downloaded;
    volatile int folderStart = -1;
    volatile int folderNow = -1;
    volatile long startedAt;
    volatile long endedAt;
    volatile String lastDate = "";

    void addListener(Listener l) {
        listeners.add(l);
    }

    void removeListener(Listener l) {
        listeners.remove(l);
    }

    void changed() {
        main.post(() -> {
            for (Listener l : listeners) l.onStatus();
        });
    }

    void begin(int folder) {
        running = true;
        phase = "准备中";
        result = "";
        note = "";
        batches = 0;
        selected = 0;
        downloaded = 0;
        folderStart = folder;
        folderNow = folder;
        lastDate = "";
        startedAt = System.currentTimeMillis();
        endedAt = 0;
        changed();
    }

    void phase(String p) {
        phase = p;
        changed();
    }

    void finish(String r, String n, int folder) {
        running = false;
        result = r;
        note = n == null ? "" : n;
        if (folder >= 0) folderNow = folder;
        endedAt = System.currentTimeMillis();
        phase = "已结束";
        changed();
    }

    long elapsedSeconds() {
        if (startedAt == 0) return 0;
        long end = endedAt == 0 ? System.currentTimeMillis() : endedAt;
        return (end - startedAt) / 1000;
    }

    String resultText() {
        switch (result) {
            case "done":
                return "完成：待下载列表已清空";
            case "paused":
                return "已暂停";
            case "stopped":
                return "已停止";
            case "safety":
                return "安全检查已停止";
            case "error":
                return "出错停止";
            default:
                return result;
        }
    }

    /** One line for the floating panel. */
    String line() {
        return String.format("第 %d 批 · 已下载 %d 张 · %s", batches + (running ? 1 : 0), downloaded, phase);
    }
}
