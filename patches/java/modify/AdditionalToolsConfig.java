package modify;

import android.app.Application;
import android.content.Context;
import android.content.SharedPreferences;

import java.lang.reflect.Method;

/**
 * Persistent configuration store for the "Additional Tools" camera tweaks panel.
 * Safely lazy-loads SharedPreferences via ActivityThread.currentApplication() so
 * tweaks work immediately from first camera launch without waiting for Settings.
 */
public final class AdditionalToolsConfig {

    private static final String PREFS_NAME = "evorez_additional_tools";

    public static final String KEY_MAX_QUALITY = "tweak_max_pixel_quality";
    public static final String KEY_ZOOM_100X = "tweak_zoom_100x";
    public static final String KEY_PHOTO_UW_TELE = "tweak_photo_uw_tele";
    public static final String KEY_AOSP_60FPS_FIX = "tweak_aosp_60fps_fix";
    public static final String KEY_4K_UHD = "tweak_4k_uhd";
    public static final String KEY_HEIC = "tweak_heic";
    public static final String KEY_AI_OCR = "tweak_ai_ocr";
    public static final String KEY_FASTSHOT = "tweak_fastshot";
    public static final String KEY_HDR_DEPURPLE = "tweak_hdr_depurple";

    private static volatile SharedPreferences sPrefs;

    private AdditionalToolsConfig() {}

    public static void init(Context ctx) {
        if (ctx != null && sPrefs == null) {
            try {
                Context appCtx = ctx.getApplicationContext();
                if (appCtx == null) {
                    appCtx = ctx;
                }
                sPrefs = appCtx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
            } catch (Throwable ignored) {
            }
        }
    }

    private static SharedPreferences getPrefs() {
        if (sPrefs != null) {
            return sPrefs;
        }
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            Method m = at.getMethod("currentApplication");
            Object app = m.invoke(null);
            if (app instanceof Application) {
                init((Application) app);
            }
        } catch (Throwable ignored) {
        }
        return sPrefs;
    }

    public static boolean getBool(String key, boolean defaultVal) {
        try {
            SharedPreferences p = getPrefs();
            if (p != null) {
                return p.getBoolean(key, defaultVal);
            }
        } catch (Throwable ignored) {
        }
        return defaultVal;
    }

    public static void setBool(Context ctx, String key, boolean value) {
        init(ctx);
        try {
            SharedPreferences p = getPrefs();
            if (p != null) {
                p.edit().putBoolean(key, value).apply();
            }
        } catch (Throwable ignored) {
        }
    }

    public static void resetDefaults(Context ctx) {
        init(ctx);
        try {
            SharedPreferences p = getPrefs();
            if (p != null) {
                p.edit()
                        .putBoolean(KEY_MAX_QUALITY, true)
                        .putBoolean(KEY_ZOOM_100X, true)
                        .putBoolean(KEY_PHOTO_UW_TELE, true)
                        .putBoolean(KEY_AOSP_60FPS_FIX, true)
                        .putBoolean(KEY_4K_UHD, true)
                        .putBoolean(KEY_HEIC, true)
                        .putBoolean(KEY_AI_OCR, true)
                        .putBoolean(KEY_FASTSHOT, false)
                        .putBoolean(KEY_HDR_DEPURPLE, true)
                        .apply();
            }
        } catch (Throwable ignored) {
        }
    }

    /** Returns 100% lossless-grade JPEG quality when Max Pixel Quality is ON, else 92. */
    public static byte getJpegQuality() {
        return getBool(KEY_MAX_QUALITY, true) ? (byte) 100 : (byte) 92;
    }

    /** Enables full multi-frame MFNR, Zoom MFNR, Front MFNR & Lens Distortion Correction. */
    public static boolean isMaxQualityEnabled() {
        return getBool(KEY_MAX_QUALITY, true);
    }

    /** Returns 100.0f when 100x Zoom is enabled, otherwise 10.0f. */
    public static float getMaxZoom() {
        return getBool(KEY_ZOOM_100X, true) ? 100.0f : 10.0f;
    }

    /**
     * Returns standard AOSP SESSION_REGULAR (0x0) when AOSP 60fps Freeze Fix is enabled,
     * avoiding MIUI's proprietary 0x803c HSR60 opmode that freezes on AOSP HALs.
     */
    public static int get60FpsOperatingMode() {
        return getBool(KEY_AOSP_60FPS_FIX, true) ? 0x0 : 0x803c;
    }

    public static boolean isPhotoUwTeleEnabled() {
        return getBool(KEY_PHOTO_UW_TELE, true);
    }

    public static boolean is4kUhdEnabled() {
        return getBool(KEY_4K_UHD, true);
    }

    public static boolean isHeicEnabled() {
        return getBool(KEY_HEIC, true);
    }

    public static boolean isAiOcrEnabled() {
        return getBool(KEY_AI_OCR, true);
    }

    public static boolean isFastshotEnabled() {
        return getBool(KEY_FASTSHOT, false);
    }

    public static boolean isHdrDepurpleEnabled() {
        return getBool(KEY_HDR_DEPURPLE, true);
    }
}
