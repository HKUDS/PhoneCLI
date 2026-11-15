package com.phoneagent.app;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Log;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * Everything that makes a fresh install usable again, in one file.
 *
 * What has to travel: the pairing identity (so the app can talk to its own
 * adbd without a new pairing dialog), the crawled app maps, the session log and
 * the model settings. The API key is included in the clear -- it is encrypted
 * with a key that lives in this device's Android Keystore, so the encrypted
 * file is useless anywhere else. The settings dialog says so before exporting.
 *
 * The Python runtime under files/chaquopy is an unpacked copy of the APK's own
 * assets; it is skipped, because it is several megabytes that the install
 * recreates by itself.
 */
final class Backup {

    private static final String TAG = "Backup";
    private static final String ZIP_SETTINGS = "settings.json";
    private static final String ZIP_FILES = "files/";

    private Backup() {
    }

    /** Writes a backup into Downloads and returns what the user should look for. */
    static String exportToDownloads(Context context) throws IOException {
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).format(new Date());
        String name = "PhoneGo-backup-" + stamp + ".zip";
        byte[] zip = buildZip(context);

        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        values.put(MediaStore.MediaColumns.MIME_TYPE, "application/zip");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
        }
        ContentResolver resolver = context.getContentResolver();
        Uri target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (target == null) {
            throw new IOException("cannot create the file in Downloads");
        }
        try (OutputStream out = resolver.openOutputStream(target)) {
            if (out == null) {
                throw new IOException("cannot open the backup file");
            }
            out.write(zip);
        }
        Log.i(TAG, "exported " + name + " (" + zip.length + " bytes)");
        return name;
    }

    private static byte[] buildZip(Context context) throws IOException {
        File dir = context.getFilesDir();
        java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(buffer)) {
            for (File file : list(dir, "")) {
                String path = ZIP_FILES + file.getAbsolutePath()
                        .substring(dir.getAbsolutePath().length() + 1);
                zip.putNextEntry(new ZipEntry(path.replace(File.separatorChar, '/')));
                try (InputStream in = new FileInputStream(file)) {
                    copy(in, zip);
                }
                zip.closeEntry();
            }
            zip.putNextEntry(new ZipEntry(ZIP_SETTINGS));
            zip.write(settingsJson(context).toString().getBytes(StandardCharsets.UTF_8));
            zip.closeEntry();
        }
        return buffer.toByteArray();
    }

    private static JSONObject settingsJson(Context context) {
        AgentConfig.Config config = AgentConfig.load(context);
        JSONObject json = new JSONObject();
        try {
            json.put("api_key", config.getApiKey());
            json.put("api_base", config.getApiBase());
            json.put("model", config.getModel());
            json.put("vision_model", config.getVisionModel());
            json.put("max_rounds", config.getMaxRounds());
            json.put("mock", config.getMock());
        } catch (Exception e) {
            Log.i(TAG, "cannot serialize the settings: " + e.getMessage());
        }
        return json;
    }

    /** Restores a backup; returns how many files came back. */
    static int restore(Context context, Uri uri) throws IOException {
        File dir = context.getFilesDir();
        String root = dir.getAbsolutePath() + File.separator;
        int restored = 0;
        JSONObject settings = null;

        try (InputStream raw = context.getContentResolver().openInputStream(uri)) {
            if (raw == null) {
                throw new IOException("cannot read the file");
            }
            try (ZipInputStream zip = new ZipInputStream(raw)) {
                ZipEntry entry;
                while ((entry = zip.getNextEntry()) != null) {
                    if (entry.isDirectory()) {
                        continue;
                    }
                    String name = entry.getName();
                    if (ZIP_SETTINGS.equals(name)) {
                        try {
                            settings = new JSONObject(readAll(zip));
                        } catch (org.json.JSONException e) {
                            throw new IOException("settings.json is not valid JSON");
                        }
                        continue;
                    }
                    if (!name.startsWith(ZIP_FILES)) {
                        continue;
                    }
                    // No "..": a crafted zip must not write outside filesDir.
                    String relative = name.substring(ZIP_FILES.length());
                    if (relative.contains("..")) {
                        continue;
                    }
                    File target = new File(root + relative);
                    File parent = target.getParentFile();
                    if (parent != null && !parent.exists() && !parent.mkdirs()) {
                        throw new IOException("cannot create " + parent);
                    }
                    try (OutputStream out = new FileOutputStream(target)) {
                        copy(zip, out);
                    }
                    restored++;
                }
            }
        }

        if (settings != null) {
            // The key goes back through AgentConfig, so it is re-encrypted with
            // this install's Keystore key instead of the old device's.
            AgentConfig.save(
                    context,
                    settings.optString("api_key", ""),
                    settings.optString("api_base", ""),
                    settings.optString("model", ""),
                    settings.optString("vision_model", ""),
                    settings.optInt("max_rounds", 0),
                    settings.optString("mock", ""));
        }
        Log.i(TAG, "restored " + restored + " file(s)");
        return restored;
    }

    /** Files to put in the zip: everything under files/ except the runtime. */
    private static List<File> list(File dir, String prefix) {
        List<File> out = new ArrayList<>();
        File[] children = dir.listFiles();
        if (children == null) {
            return out;
        }
        for (File child : children) {
            String path = prefix.isEmpty() ? child.getName() : prefix + "/" + child.getName();
            if (child.isDirectory()) {
                if (path.startsWith("chaquopy") || path.contains("__pycache__")) {
                    continue;
                }
                out.addAll(list(child, path));
            } else if (keep(child.getName())) {
                out.add(child);
            }
        }
        return out;
    }

    /**
     * Scratch files do not belong in a backup: the last task leaves screenshots
     * and trees behind (2 MB of them), and a half-written crawl is not a map.
     */
    private static boolean keep(String name) {
        if ("agent.secret".equals(name) || "pa-helper.jar".equals(name)) {
            return false;
        }
        if (name.startsWith("frame") && name.endsWith(".png")) {
            return false;
        }
        if (name.startsWith("tree") && name.endsWith(".xml")) {
            return false;
        }
        return !name.endsWith(".partial") && !name.contains(".bak");
    }

    private static String readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        copy(in, out);
        return out.toString("UTF-8");
    }

    private static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] chunk = new byte[64 * 1024];
        int read;
        while ((read = in.read(chunk)) > 0) {
            out.write(chunk, 0, read);
        }
    }
}
