package com.phoneagent.helper;

import android.app.ActivityOptions;
import android.content.ComponentName;
import android.content.Intent;
import android.content.Context;
import android.content.ContextWrapper;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.os.Looper;
import android.view.Surface;

import java.io.BufferedReader;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.concurrent.Executor;

/**
 * Creates a hostable virtual display from a shell-uid process.
 *
 * The public DisplayManager API stamps the display with the *context's* package
 * name and rejects a mismatch with the calling uid, which is why a shell
 * process cannot use it. Calling DisplayManagerGlobal directly lets us pass our
 * own context, so the wrapper below answers "com.android.shell" (uid 2000's
 * package) and the check passes.
 */
public final class Main {

    private static Context systemContext() throws Exception {
        Class<?> activityThread = Class.forName("android.app.ActivityThread");
        Method systemMain = activityThread.getDeclaredMethod("systemMain");
        systemMain.setAccessible(true);
        Object thread = systemMain.invoke(null);
        Method getSystemContext = activityThread.getDeclaredMethod("getSystemContext");
        getSystemContext.setAccessible(true);
        return (Context) getSystemContext.invoke(thread);
    }

    /**
     * Find createVirtualDisplay by shape, not by exact signature.
     *
     * The last parameter is a Handler on Android 13 and earlier, an Executor
     * from Android 14 on -- looking the method up by exact signature is how this
     * helper used to fail with NoSuchMethodException on API 33. Both forms
     * accept null for that parameter, so only the position matters.
     */
    private static Method findCreateVirtualDisplay(Class<?> globalClass, Class<?> configClass)
            throws NoSuchMethodException {
        for (Method method : globalClass.getDeclaredMethods()) {
            if (!method.getName().equals("createVirtualDisplay")) continue;
            Class<?>[] params = method.getParameterTypes();
            if (params.length < 5) continue;
            boolean configInThird = params[2] == configClass
                    || params[2].getName().equals(configClass.getName());
            if (!configInThird) continue;
            return method;
        }
        throw new NoSuchMethodException("no createVirtualDisplay taking " + configClass.getSimpleName());
    }

    private static String describeCreateCandidates(Class<?> globalClass) {
        StringBuilder out = new StringBuilder();
        for (Method method : globalClass.getDeclaredMethods()) {
            if (!method.getName().equals("createVirtualDisplay")) continue;
            out.append("\n    ").append(method);
        }
        return out.length() == 0 ? "(none)" : out.toString();
    }

    public static void main(String[] args) {
        Looper.prepareMainLooper();
        try {
            run(args);
        } catch (Throwable t) {
            System.out.println("ERROR " + t);
            t.printStackTrace(System.out);
            System.out.flush();
            System.exit(4);
        }
    }

    /** Start an activity on the display this process created. */
    private static boolean launchOn(Context context, VirtualDisplay display, String component) {
        try {
            String[] parts = component.split("/");
            Intent intent = new Intent();
            intent.setComponent(new ComponentName(parts[0], parts[1]));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ActivityOptions opts = ActivityOptions.makeBasic();
            opts.setLaunchDisplayId(display.getDisplay().getDisplayId());
            // startActivity validates the *package* against the calling uid, and
            // a bare app_process context calls itself "android" whatever this
            // wrapper overrides -- the check reads the base ContextImpl, not the
            // wrapper. A Context created for com.android.shell is the only one
            // that passes and still carries this process's display token.
            Context owner;
            try {
                owner = context.createPackageContext("com.android.shell", 0);
            } catch (Throwable t) {
                owner = context;
            }
            owner.startActivity(intent, opts.toBundle());
            return true;
        } catch (Throwable t) {
            System.out.println("LAUNCH_ERROR " + t);
            return false;
        }
    }

    private static void run(String[] args) throws Exception {
        int width = args.length > 0 ? Integer.parseInt(args[0]) : 1280;
        int height = args.length > 1 ? Integer.parseInt(args[1]) : 2772;
        int dpi = args.length > 2 ? Integer.parseInt(args[2]) : 520;
        String name = args.length > 3 ? args[3] : "pa-helper";

        Context context = new ContextWrapper(systemContext()) {
            @Override
            public String getPackageName() {
                return "com.android.shell";
            }

            @Override
            public String getOpPackageName() {
                // the display service validates *this* one against the calling
                // uid; leaving it as the base context's "android" throws
                // "packageName must match the calling uid" on Android 13
                return "com.android.shell";
            }

            @Override
            public Context getApplicationContext() {
                return this;
            }
        };

        ImageReader reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
        Surface surface = reader.getSurface();

        Class<?> builderClass =
                Class.forName("android.hardware.display.VirtualDisplayConfig$Builder");
        Constructor<?> ctor = builderClass.getDeclaredConstructor(
                String.class, int.class, int.class, int.class);
        ctor.setAccessible(true);
        Object builder = ctor.newInstance(name, width, height, dpi);

        Method setSurface = builderClass.getDeclaredMethod("setSurface", Surface.class);
        setSurface.setAccessible(true);
        setSurface.invoke(builder, surface);

        Method setFlags = builderClass.getDeclaredMethod("setFlags", int.class);
        setFlags.setAccessible(true);
        // PUBLIC puts the display in the default display group, where the home
        // task renders on every member -- which is why a brand-new "empty"
        // display showed the phone's home screen and looked like a mirror.
        // OWN_DISPLAY_GROUP separates it; the value is read from DisplayManager
        // instead of hardcoded, because guessing it wrong silently sets some
        // other flag.
        // PUBLIC puts this display in the default display *group*, and the
        // home task renders on every member of a group. That is why a fresh
        // "empty" display shows the phone's home screen -- it is not a mirror,
        // it is the launcher drawn in both places.
        //
        // VIRTUAL_DISPLAY_FLAG_OWN_DISPLAY_GROUP (0x800) would isolate it, and
        // it does stop the launcher from appearing -- but on this device an
        // own-group display then renders nothing at all (NOFRAME even after
        // `am start --display N`), so it is not a usable trade. The practical
        // rule is therefore: always launch something on the display.
        // `own-group` is an experiment switch, and the experiment failed:
        // an own-group display is private to the process that created it, and
        // nothing this process can do gets an activity onto it.
        //   am start --display N            -> SecurityException (launchDisplayId)
        //   startActivity from this process -> "package=android does not belong
        //                                      to uid=2000", and past that the
        //                                      display check wants
        //                                      INTERNAL_SYSTEM_WINDOW
        // So PUBLIC stays, and with it the mirroring: a task really does take
        // the phone's screen. Only root (or a signature permission) changes it.
        boolean ownGroup = args.length > 4 && args[4].equals("own-group");
        int flags = ownGroup
                ? 0x00000800   // VIRTUAL_DISPLAY_FLAG_OWN_DISPLAY_GROUP
                : DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC;
        // The virtual display must not take the input focus away from the
        // phone's own screen: when it did, the first tap on the app only
        // restored focus instead of pressing the button.
        try {
            flags |= DisplayManager.class
                    .getField("VIRTUAL_DISPLAY_FLAG_STEAL_TOP_FOCUS_DISABLED").getInt(null);
            System.out.println("INFO top-focus stealing disabled: 0x" + Integer.toHexString(flags));
        } catch (ReflectiveOperationException e) {
            System.out.println("WARN STEAL_TOP_FOCUS_DISABLED unavailable: " + e);
        }
        setFlags.invoke(builder, flags);

        // canHostTasks is not a setter: DisplayManager derives it from the
        // config. Without a home-capable display, `am start --display N`
        // creates a task that never becomes active, windows stay
        // focused=false/active=false, and an accessibility service gets a null
        // root for every window on it (and the launcher refuses to start).
        try {
            Method setHomeSupported = builderClass.getDeclaredMethod("setHomeSupported", boolean.class);
            setHomeSupported.setAccessible(true);
            setHomeSupported.invoke(builder, true);
        } catch (NoSuchMethodException ignored) {
            System.out.println("WARN setHomeSupported unavailable on this platform");
        }
        try {
            Method setIgnore = builderClass.getDeclaredMethod(
                    "setIgnoreActivitySizeRestrictions", boolean.class);
            setIgnore.setAccessible(true);
            setIgnore.invoke(builder, true);
        } catch (NoSuchMethodException ignored) {
            System.out.println("WARN setIgnoreActivitySizeRestrictions unavailable");
        }
        // Without this an empty display shows a copy of the phone's screen: the
        // ImageReader then hands back the default display's pixels instead of
        // black, which is how a "screenshot" of the virtual display ended up
        // showing the chat UI.
        try {
            Method setMirror = builderClass.getDeclaredMethod(
                    "setWindowManagerMirroringEnabled", boolean.class);
            setMirror.setAccessible(true);
            setMirror.invoke(builder, false);
        } catch (NoSuchMethodException ignored) {
            System.out.println("WARN setWindowManagerMirroringEnabled unavailable");
        }

        Method setUniqueId = builderClass.getDeclaredMethod("setUniqueId", String.class);
        setUniqueId.setAccessible(true);
        setUniqueId.invoke(builder, "virtual:com.android.shell/" + name);

        Method build = builderClass.getDeclaredMethod("build");
        build.setAccessible(true);
        Object config = build.invoke(builder);

        Class<?> globalClass = Class.forName("android.hardware.display.DisplayManagerGlobal");
        Method getInstance = globalClass.getDeclaredMethod("getInstance");
        getInstance.setAccessible(true);
        Object global = getInstance.invoke(null);

        VirtualDisplay display;
        try {
            Method create = findCreateVirtualDisplay(globalClass, config.getClass());
            System.out.println("INFO createVirtualDisplay -> " + create);
            create.setAccessible(true);
            // The tail differs by version: Handler on 13 and earlier, Executor on
            // 14+, plus an extra Context on some builds. Everything after the
            // config is optional, so pass nulls and let the platform default.
            Object[] callArgs = new Object[create.getParameterCount()];
            Class<?>[] paramTypes = create.getParameterTypes();
            for (int i = 0; i < paramTypes.length; i++) {
                // every Context argument is the attribution context; on Android
                // 13 the trailing one is checked against the calling uid, and a
                // null makes it throw "packageName must match the calling uid"
                if (paramTypes[i] == Context.class) callArgs[i] = context;
            }
            callArgs[0] = context;
            callArgs[2] = config;
            display = (VirtualDisplay) create.invoke(global, callArgs);
        } catch (NoSuchMethodException | IllegalStateException e) {
            System.out.println("ERROR no usable createVirtualDisplay: " + e);
            System.out.println("ERROR candidates: " + describeCreateCandidates(globalClass));
            System.exit(4);
            return;
        } catch (java.lang.reflect.InvocationTargetException e) {
            System.out.println(("probe".equals(name) ? "PROBE error: " : "ERROR createVirtualDisplay failed: ")
                    + e.getCause());
            if ("probe".equals(name)) { System.out.flush(); System.exit(4); return; }
            if (e.getCause() != null) e.getCause().printStackTrace(System.out);
            System.exit(4);
            return;
        }
        if (display == null) {
            System.out.println("ERROR createVirtualDisplay returned null");
            System.exit(5);
        }
        if ("probe".equals(name)) {
            // Capability check, not a real display: a 2x2 surface costs nothing
            // and tells us whether this Android build can host one at all.
            System.out.println("PROBE ok display=" + display.getDisplay().getDisplayId()
                    + " android=" + android.os.Build.VERSION.SDK_INT
                    + " size=" + width + "x" + height);
            System.out.flush();
            display.release();
            // give the reading side a moment: exiting immediately closes the
            // stream and the caller sees no verdict at all
            try { Thread.sleep(800); } catch (InterruptedException ignored) { }
            reader.close();
            System.exit(0);
        }
        System.out.println("OK display=" + display.getDisplay().getDisplayId()
                + " size=" + width + "x" + height);
        System.out.flush();

        // The display's only consumer is this ImageReader: screencap cannot
        // resolve a display created by another process, so frames are grabbed
        // here and written as PNG on demand.
        // the main thread blocks on stdin below, so the listener needs its own
        // looper or it never runs
        android.os.HandlerThread frameThread = new android.os.HandlerThread("pa-frames");
        frameThread.start();
        // Keep the pixels, not the Image: repeatedly asking for a frame while
        // the screen is unchanged must still return the last picture.
        final Bitmap[] latest = new Bitmap[1];
        reader.setOnImageAvailableListener(r -> {
            Image image = r.acquireLatestImage();
            if (image == null) return;
            try {
                Bitmap bitmap = Bitmap.createBitmap(
                        image.getWidth(), image.getHeight(), Bitmap.Config.ARGB_8888);
                bitmap.copyPixelsFromBuffer(image.getPlanes()[0].getBuffer());
                synchronized (latest) {
                    if (latest[0] != null) latest[0].recycle();
                    latest[0] = bitmap;
                }
            } finally {
                image.close();
            }
        }, new android.os.Handler(frameThread.getLooper()));

        // --launch=<pkg>/<activity> turns this into a self-contained experiment:
        // create the display, start the activity from inside this process (the
        // only context that owns the display token), wait, report whether a
        // frame ever arrived, and exit. No stdin involved.
        String launchAt = "";
        for (String a : args) {
            if (a.startsWith("--launch=")) launchAt = a.substring("--launch=".length());
        }
        if (!launchAt.isEmpty()) {
            boolean started = launchOn(context, display, launchAt);
            System.out.println(started ? "SELFTEST LAUNCHED " + launchAt
                                      : "SELFTEST LAUNCH_FAILED " + launchAt);
            System.out.flush();
            for (int i = 0; i < 6; i++) {
                Thread.sleep(1000);
                synchronized (latest) {
                    if (latest[0] != null) break;
                }
            }
            Bitmap bmp;
            synchronized (latest) {
                bmp = latest[0];
            }
            if (bmp == null) {
                System.out.println("SELFTEST NOFRAME");
            } else {
                try (FileOutputStream out = new FileOutputStream("/data/local/tmp/own.png")) {
                    bmp.compress(Bitmap.CompressFormat.PNG, 100, out);
                }
                System.out.println("SELFTEST FRAME " + bmp.getWidth() + "x" + bmp.getHeight());
            }
            System.out.flush();
            display.release();
            Thread.sleep(800);
            reader.close();
            System.exit(0);
        }

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.equals("stop")) {
                break;
            }
            if (line.startsWith("launch")) {
                // An own-group display is private to the process that created
                // it: `am start --display N` is refused with a SecurityException
                // ("launchDisplayId"), because the shell command is not the
                // display's owner. Starting the activity from inside this
                // process carries the right token.
                String comp = line.length() > 6 ? line.substring(6).trim() : "";
                System.out.println(launchOn(context, display, comp)
                        ? "LAUNCHED " + comp : "LAUNCH_FAILED " + comp);
                System.out.flush();
                continue;
            }
            if (line.startsWith("frame")) {
                String path = line.length() > 6 ? line.substring(6).trim() : "/data/local/tmp/pa-frame.png";
                Bitmap bitmap;
                synchronized (latest) {
                    bitmap = latest[0];
                }
                if (bitmap == null) {
                    System.out.println("NOFRAME");
                    System.out.flush();
                    continue;
                }
                synchronized (latest) {
                    try (FileOutputStream out = new FileOutputStream(path)) {
                        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out);
                    }
                    System.out.println("FRAME " + path + " " + bitmap.getWidth()
                            + "x" + bitmap.getHeight());
                }
                System.out.flush();
            }
        }
        display.release();
        reader.close();
        System.out.println("RELEASED");
        System.out.flush();
    }
}
