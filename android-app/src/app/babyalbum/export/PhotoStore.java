package app.babyalbum.export;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;

/** Counts the photos the official app saved, through MediaStore (read-only). */
final class PhotoStore {
    private PhotoStore() {
    }

    static String permission() {
        return Build.VERSION.SDK_INT >= 33 ? Manifest.permission.READ_MEDIA_IMAGES
                : Manifest.permission.READ_EXTERNAL_STORAGE;
    }

    static boolean canRead(Context c) {
        return c.checkSelfPermission(permission()) == PackageManager.PERMISSION_GRANTED;
    }

    /** Number of images in DCIM/Camera/海马爸比, or -1 when unknown. */
    static int count(Context c) {
        if (!canRead(c)) return -1;
        Uri uri = MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL);
        String[] cols = {MediaStore.MediaColumns._ID};
        String sel = MediaStore.MediaColumns.RELATIVE_PATH + "=?";
        String[] args = {Haima.EXPORT_RELATIVE_PATH};
        try (Cursor cur = c.getContentResolver().query(uri, cols, sel, args, null)) {
            return cur == null ? -1 : cur.getCount();
        } catch (RuntimeException e) {
            return -1;
        }
    }
}
