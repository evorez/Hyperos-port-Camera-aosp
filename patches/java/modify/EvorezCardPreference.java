package modify;

import android.content.Context;
import android.content.Intent;
import android.graphics.RenderEffect;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Build;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.preference.Preference;
import androidx.preference.PreferenceGroup;
import androidx.preference.PreferenceScreen;
import androidx.preference.PreferenceViewHolder;

/**
 * Frosted glassy-blur bottom card for Universal Xiomeme-Leica Camera mod By Evorez.
 * Uses Android 12+ RenderEffect ambient blur backdrop + translucent frosted glass surface
 * and side-by-side glassmorphic pill buttons for GitHub & Telegram.
 */
public class EvorezCardPreference extends Preference {

    private static final String CARD_KEY = "evorez_mod_card_bottom";
    private static final String TELEGRAM_URL = "https://t.me/evorezlabs";
    private static final String GITHUB_URL = "https://github.com/evorez";

    private static final String TITLE_TEXT =
            "Universal Xiomeme-Leica Camera mod By \uD835\uDE74\uD835\uDE9F\uD835\uDE98\uD835\uDE9B\uD835\uDE8E\uD835\uDEA3";
    private static final String SUBTITLE_TEXT =
            "For more Builds join https://t.me/evorezlabs / Evorez.in";

    public EvorezCardPreference(Context context) {
        super(context);
        setKey(CARD_KEY);
        setOrder(9999);
        setSelectable(false);
        setPersistent(false);
    }

    /**
     * Removes any legacy HolyBear / third-party credit preferences and attaches the
     * Evorez frosted glass card cleanly at the bottom of the PreferenceScreen.
     */
    public static void attachTo(PreferenceScreen screen) {
        if (screen == null) {
            return;
        }
        try {
            Context ctx = screen.getContext();
            if (ctx != null) {
                AdditionalToolsConfig.init(ctx);
            }
            removeLegacyCredits(screen);
            if (ctx != null && screen.findPreference(AdditionalToolsPreference.PREF_KEY) == null) {
                screen.addPreference(new AdditionalToolsPreference(ctx));
            }
            if (ctx != null && screen.findPreference(CARD_KEY) == null) {
                screen.addPreference(new EvorezCardPreference(ctx));
            }
        } catch (Throwable ignored) {
        }
    }

    private static void removeLegacyCredits(PreferenceGroup group) {
        if (group == null) {
            return;
        }
        int count = group.getPreferenceCount();
        for (int i = count - 1; i >= 0; i--) {
            Preference pref = group.getPreference(i);
            if (pref == null) {
                continue;
            }
            String key = pref.getKey();
            if (CARD_KEY.equals(key) || AdditionalToolsPreference.PREF_KEY.equals(key)) {
                continue;
            }
            if (isLegacyCreditPref(pref)) {
                group.removePreference(pref);
                continue;
            }
            if (pref instanceof PreferenceGroup) {
                removeLegacyCredits((PreferenceGroup) pref);
            }
        }
    }

    private static boolean isLegacyCreditPref(Preference pref) {
        String key = pref.getKey();
        if ("pref_privacy".equals(key)
                || "pref_lab".equals(key)
                || "pref_universal".equals(key)) {
            return true;
        }
        String title = pref.getTitle() != null ? pref.getTitle().toString().toLowerCase() : "";
        String summary = pref.getSummary() != null ? pref.getSummary().toString().toLowerCase() : "";
        String combined = title + " " + summary;
        return combined.contains("holybear")
                || combined.contains("holy bear")
                || combined.contains("paypal:")
                || combined.contains("sevtinge")
                || combined.contains("itzdfplayer")
                || combined.contains("modification description");
    }

    @Override
    public void onBindViewHolder(PreferenceViewHolder holder) {
        super.onBindViewHolder(holder);
        if (holder == null || !(holder.itemView instanceof ViewGroup)) {
            return;
        }
        ViewGroup root = (ViewGroup) holder.itemView;
        final Context ctx = root.getContext();
        if (ctx == null) {
            return;
        }

        root.setBackgroundColor(0x00000000);
        root.setPadding(0, 0, 0, 0);
        root.setClickable(false);
        root.setFocusable(false);
        root.removeAllViews();

        // Outer spacing wrapper
        FrameLayout outer = new FrameLayout(ctx);
        outer.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        outer.setPadding(dp(ctx, 16), dp(ctx, 14), dp(ctx, 16), dp(ctx, 26));
        outer.setClipToPadding(false);
        outer.setClipChildren(false);

        // Layered glass container (blurred ambient glow behind + frosted glass surface on top)
        FrameLayout glassStack = new FrameLayout(ctx);
        glassStack.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        ));
        GradientDrawable clipShape = new GradientDrawable();
        clipShape.setCornerRadius(dp(ctx, 24));
        clipShape.setColor(0x01000000);
        glassStack.setBackground(clipShape);
        glassStack.setClipToOutline(true);

        // Layer 0: Blurred luminous ambient backdrop (real GPU blur on Android 12-16)
        View blurGlow = new View(ctx);
        blurGlow.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        GradientDrawable glowBg = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0x7054214C, 0x50231B36, 0x60381632}
        );
        glowBg.setCornerRadius(dp(ctx, 24));
        blurGlow.setBackground(glowBg);
        if (Build.VERSION.SDK_INT >= 31) {
            try {
                blurGlow.setRenderEffect(RenderEffect.createBlurEffect(
                        dp(ctx, 22),
                        dp(ctx, 22),
                        Shader.TileMode.CLAMP
                ));
            } catch (Throwable ignored) {
            }
        }
        glassStack.addView(blurGlow);

        // Layer 1: Translucent frosted glass card body
        LinearLayout card = new LinearLayout(ctx);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        ));
        card.setPadding(dp(ctx, 18), dp(ctx, 16), dp(ctx, 18), dp(ctx, 16));

        GradientDrawable cardBg = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0xAD231828, 0xBA131019, 0xC60B0A10}
        );
        cardBg.setCornerRadius(dp(ctx, 24));
        cardBg.setStroke(dp(ctx, 1), 0x45FFFFFF);
        card.setBackground(cardBg);
        card.setElevation(dp(ctx, 8));

        // Subtle glass specular top highlight line
        View topSheen = new View(ctx);
        LinearLayout.LayoutParams sheenLp = new LinearLayout.LayoutParams(
                dp(ctx, 64),
                dp(ctx, 3)
        );
        sheenLp.gravity = Gravity.CENTER_HORIZONTAL;
        sheenLp.bottomMargin = dp(ctx, 12);
        topSheen.setLayoutParams(sheenLp);
        GradientDrawable sheenBg = new GradientDrawable();
        sheenBg.setCornerRadius(dp(ctx, 2));
        sheenBg.setColor(0x38FFFFFF);
        topSheen.setBackground(sheenBg);
        card.addView(topSheen);

        // Tapping the card itself opens Telegram
        card.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openUrl(ctx, TELEGRAM_URL);
            }
        });

        // Title: Universal Xiomeme-Leica Camera mod By 𝙴𝚟𝚘𝚛𝚎𝚣
        TextView titleView = new TextView(ctx);
        titleView.setText(TITLE_TEXT);
        titleView.setTextColor(0xFFFFFFFF);
        titleView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16.5f);
        titleView.setTypeface(Typeface.DEFAULT_BOLD);
        titleView.setLineSpacing(dp(ctx, 3), 1.0f);
        card.addView(titleView);

        // Subtitle: For more Builds join https://t.me/evorezlabs / Evorez.in
        TextView subView = new TextView(ctx);
        subView.setText(SUBTITLE_TEXT);
        subView.setTextColor(0xCCFFFFFF);
        subView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13.0f);
        subView.setLineSpacing(dp(ctx, 2), 1.0f);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        subLp.topMargin = dp(ctx, 8);
        subView.setLayoutParams(subLp);
        subView.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openUrl(ctx, TELEGRAM_URL);
            }
        });
        card.addView(subView);

        // Frosted glass hairline divider
        View divider = new View(ctx);
        LinearLayout.LayoutParams divLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(ctx, 1)
        );
        divLp.topMargin = dp(ctx, 14);
        divLp.bottomMargin = dp(ctx, 14);
        divider.setLayoutParams(divLp);
        divider.setBackgroundColor(0x26FFFFFF);
        card.addView(divider);

        // Translucent frosted pill tray matching the reference image
        LinearLayout pillTray = new LinearLayout(ctx);
        pillTray.setOrientation(LinearLayout.HORIZONTAL);
        pillTray.setGravity(Gravity.CENTER);
        pillTray.setLayoutParams(new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));
        pillTray.setPadding(dp(ctx, 6), dp(ctx, 6), dp(ctx, 6), dp(ctx, 6));

        GradientDrawable trayBg = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0x8C18121E, 0xA80B090F}
        );
        trayBg.setShape(GradientDrawable.RECTANGLE);
        trayBg.setCornerRadius(dp(ctx, 28));
        trayBg.setStroke(dp(ctx, 1), 0x33FFFFFF);
        pillTray.setBackground(trayBg);

        // Left frosted glass pill button: GitHub (@evorez)
        TextView githubBtn = buildPillButton(ctx, "GitHub", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openUrl(ctx, GITHUB_URL);
            }
        });
        LinearLayout.LayoutParams ghLp = new LinearLayout.LayoutParams(
                0,
                dp(ctx, 44),
                1.0f
        );
        ghLp.rightMargin = dp(ctx, 5);
        githubBtn.setLayoutParams(ghLp);
        pillTray.addView(githubBtn);

        // Right frosted glass pill button: Telegram (https://t.me/evorezlabs)
        TextView tgBtn = buildPillButton(ctx, "Telegram", new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openUrl(ctx, TELEGRAM_URL);
            }
        });
        LinearLayout.LayoutParams tgLp = new LinearLayout.LayoutParams(
                0,
                dp(ctx, 44),
                1.0f
        );
        tgLp.leftMargin = dp(ctx, 5);
        tgBtn.setLayoutParams(tgLp);
        pillTray.addView(tgBtn);

        card.addView(pillTray);
        glassStack.addView(card);
        outer.addView(glassStack);
        root.addView(outer);
    }

    private static TextView buildPillButton(Context ctx, String label, View.OnClickListener listener) {
        TextView btn = new TextView(ctx);
        btn.setText(label);
        btn.setTextColor(0xFFFFFFFF);
        btn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14.5f);
        btn.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        btn.setGravity(Gravity.CENTER);
        btn.setClickable(true);
        btn.setFocusable(true);

        GradientDrawable normal = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xB832243A, 0xB01A1522}
        );
        normal.setShape(GradientDrawable.RECTANGLE);
        normal.setCornerRadius(dp(ctx, 22));
        normal.setStroke(dp(ctx, 1), 0x48FFFFFF);

        GradientDrawable pressed = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xDC483254, 0xCC261F31}
        );
        pressed.setShape(GradientDrawable.RECTANGLE);
        pressed.setCornerRadius(dp(ctx, 22));
        pressed.setStroke(dp(ctx, 1), 0x6EFFFFFF);

        StateListDrawable selector = new StateListDrawable();
        selector.addState(new int[]{android.R.attr.state_pressed}, pressed);
        selector.addState(new int[]{}, normal);
        btn.setBackground(selector);
        btn.setOnClickListener(listener);
        return btn;
    }

    private static void openUrl(Context ctx, String url) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(intent);
        } catch (Throwable ignored) {
        }
    }

    private static int dp(Context ctx, int value) {
        float density = ctx.getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }
}
