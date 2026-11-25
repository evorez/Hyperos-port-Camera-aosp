package modify;

import android.app.AlertDialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.RenderEffect;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Build;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.CompoundButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import androidx.preference.Preference;
import androidx.preference.PreferenceViewHolder;

/**
 * Frosted-glass "Additional Tools" entry in Camera Settings that opens a sleek dark
 * glassmorphic tweaks panel for 100x Zoom, 0.5x & Telephoto, AOSP 60fps Fix, and Pro tweaks.
 */
public class AdditionalToolsPreference extends Preference {

    public static final String PREF_KEY = "evorez_additional_tools_entry";

    public AdditionalToolsPreference(Context context) {
        super(context);
        AdditionalToolsConfig.init(context);
        setKey(PREF_KEY);
        setOrder(9998);
        setSelectable(false);
        setPersistent(false);
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
        AdditionalToolsConfig.init(ctx);

        root.setBackgroundColor(0x00000000);
        root.setPadding(0, 0, 0, 0);
        root.setClickable(false);
        root.setFocusable(false);
        root.removeAllViews();

        FrameLayout outer = new FrameLayout(ctx);
        outer.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        outer.setPadding(dp(ctx, 16), dp(ctx, 12), dp(ctx, 16), dp(ctx, 4));
        outer.setClipToPadding(false);

        FrameLayout glassBox = new FrameLayout(ctx);
        glassBox.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        ));
        GradientDrawable clipBg = new GradientDrawable();
        clipBg.setCornerRadius(dp(ctx, 20));
        clipBg.setColor(0x01000000);
        glassBox.setBackground(clipBg);
        glassBox.setClipToOutline(true);

        View blurBg = new View(ctx);
        blurBg.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));
        GradientDrawable glow = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0x583E1F47, 0x441B1930}
        );
        glow.setCornerRadius(dp(ctx, 20));
        blurBg.setBackground(glow);
        if (Build.VERSION.SDK_INT >= 31) {
            try {
                blurBg.setRenderEffect(RenderEffect.createBlurEffect(
                        dp(ctx, 16), dp(ctx, 16), Shader.TileMode.CLAMP
                ));
            } catch (Throwable ignored) {
            }
        }
        glassBox.addView(blurBg);

        LinearLayout row = new LinearLayout(ctx);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        ));
        row.setPadding(dp(ctx, 18), dp(ctx, 15), dp(ctx, 18), dp(ctx, 15));

        GradientDrawable normalBg = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0xAD22172A, 0xBF12101A}
        );
        normalBg.setCornerRadius(dp(ctx, 20));
        normalBg.setStroke(dp(ctx, 1), 0x42FFFFFF);

        GradientDrawable pressedBg = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0xCC332340, 0xCC1C1826}
        );
        pressedBg.setCornerRadius(dp(ctx, 20));
        pressedBg.setStroke(dp(ctx, 1), 0x66FFFFFF);

        StateListDrawable selector = new StateListDrawable();
        selector.addState(new int[]{android.R.attr.state_pressed}, pressedBg);
        selector.addState(new int[]{}, normalBg);
        row.setBackground(selector);
        row.setClickable(true);
        row.setFocusable(true);

        LinearLayout textCol = new LinearLayout(ctx);
        textCol.setOrientation(LinearLayout.VERTICAL);
        textCol.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));

        TextView title = new TextView(ctx);
        title.setText("Additional Tools");
        title.setTextColor(0xFFFFFFFF);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16.0f);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        textCol.addView(title);

        TextView subtitle = new TextView(ctx);
        subtitle.setText("100\u00D7 Zoom \u00B7 0.5\u00D7 & Telephoto \u00B7 60fps Fix \u00B7 Pro Tweaks");
        subtitle.setTextColor(0xB8FFFFFF);
        subtitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.2f);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        subLp.topMargin = dp(ctx, 3);
        subtitle.setLayoutParams(subLp);
        textCol.addView(subtitle);

        TextView chevron = new TextView(ctx);
        chevron.setText("\u203A");
        chevron.setTextColor(0xCCFFFFFF);
        chevron.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24.0f);
        chevron.setPadding(dp(ctx, 12), 0, 0, 0);

        row.addView(textCol);
        row.addView(chevron);
        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showTweaksDialog(ctx);
            }
        });

        glassBox.addView(row);
        outer.addView(glassBox);
        root.addView(outer);
    }

    private static void showTweaksDialog(final Context ctx) {
        try {
            LinearLayout panel = new LinearLayout(ctx);
            panel.setOrientation(LinearLayout.VERTICAL);
            panel.setPadding(dp(ctx, 20), dp(ctx, 20), dp(ctx, 20), dp(ctx, 18));

            GradientDrawable dialogBg = new GradientDrawable(
                    GradientDrawable.Orientation.TL_BR,
                    new int[]{0xF21E1424, 0xF5110E17, 0xFA09090D}
            );
            dialogBg.setCornerRadius(dp(ctx, 24));
            dialogBg.setStroke(dp(ctx, 1), 0x45FFFFFF);
            panel.setBackground(dialogBg);

            // Header
            TextView header = new TextView(ctx);
            header.setText("Additional Tools");
            header.setTextColor(0xFFFFFFFF);
            header.setTextSize(TypedValue.COMPLEX_UNIT_SP, 19.0f);
            header.setTypeface(Typeface.DEFAULT_BOLD);
            panel.addView(header);

            TextView desc = new TextView(ctx);
            desc.setText("Camera tweaks & lens unlocks — reopen camera if needed to apply session changes.");
            desc.setTextColor(0xB3FFFFFF);
            desc.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f);
            LinearLayout.LayoutParams descLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
            );
            descLp.topMargin = dp(ctx, 4);
            descLp.bottomMargin = dp(ctx, 14);
            desc.setLayoutParams(descLp);
            panel.addView(desc);

            ScrollView scroll = new ScrollView(ctx);
            LinearLayout list = new LinearLayout(ctx);
            list.setOrientation(LinearLayout.VERTICAL);

            addTweakRow(ctx, list,
                    "Max Pixel Quality (100% JPEG + MFNR)",
                    "Zero JPEG compression loss, multi-frame MFNR, Zoom MFNR, Front MFNR & LDC",
                    AdditionalToolsConfig.KEY_MAX_QUALITY, true);

            addTweakRow(ctx, list,
                    "100\u00D7 Super Zoom",
                    "Unlock up to 100\u00D7 digital & hybrid zoom in Photo mode",
                    AdditionalToolsConfig.KEY_ZOOM_100X, true);

            addTweakRow(ctx, list,
                    "0.5\u00D7 Ultra-Wide & Telephoto Zoom",
                    "Show 0.5\u00D7 / 1\u00D7 / 2\u00D7 / 5\u00D7 lens buttons in Photo & Video zoom",
                    AdditionalToolsConfig.KEY_PHOTO_UW_TELE, true);

            addTweakRow(ctx, list,
                    "AOSP 60fps Freeze Fix",
                    "Use standard AOSP capture session for 60fps video (prevents screen freeze)",
                    AdditionalToolsConfig.KEY_AOSP_60FPS_FIX, true);

            addTweakRow(ctx, list,
                    "4K UHD Video Unlock",
                    "Enable 4K UHD video quality option across AOSP HALs",
                    AdditionalToolsConfig.KEY_4K_UHD, true);

            addTweakRow(ctx, list,
                    "HEIF / HEIC Capture",
                    "Enable high-efficiency HEIC photo capture toggle",
                    AdditionalToolsConfig.KEY_HEIC, true);

            addTweakRow(ctx, list,
                    "AI OCR & Lens Dirty Detect",
                    "Enable viewfinder text scanner and smart lens alerts",
                    AdditionalToolsConfig.KEY_AI_OCR, true);

            addTweakRow(ctx, list,
                    "Fastshot & HQ QuickShot",
                    "Optional speed-priority shutter (keep OFF for sharpest multi-frame quality)",
                    AdditionalToolsConfig.KEY_FASTSHOT, false);

            addTweakRow(ctx, list,
                    "HDR Depurple & Front AI",
                    "Software chromatic aberration cleanup & front AI optimization",
                    AdditionalToolsConfig.KEY_HDR_DEPURPLE, true);

            scroll.addView(list);
            LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    dp(ctx, 360)
            );
            scroll.setLayoutParams(scrollLp);
            panel.addView(scroll);

            // Bottom pill buttons: Reset & Done
            LinearLayout btnRow = new LinearLayout(ctx);
            btnRow.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams btnRowLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
            );
            btnRowLp.topMargin = dp(ctx, 14);
            btnRow.setLayoutParams(btnRowLp);

            final AlertDialog dialog = new AlertDialog.Builder(ctx)
                    .setView(panel)
                    .create();

            TextView resetBtn = buildDialogPill(ctx, "Reset Defaults", 0x99241B2B);
            LinearLayout.LayoutParams rLp = new LinearLayout.LayoutParams(0, dp(ctx, 42), 1.0f);
            rLp.rightMargin = dp(ctx, 6);
            resetBtn.setLayoutParams(rLp);
            resetBtn.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    AdditionalToolsConfig.resetDefaults(ctx);
                    Toast.makeText(ctx, "Additional Tools reset to defaults", Toast.LENGTH_SHORT).show();
                    dialog.dismiss();
                }
            });

            TextView doneBtn = buildDialogPill(ctx, "Save & Close", 0xCC3B2647);
            LinearLayout.LayoutParams dLp = new LinearLayout.LayoutParams(0, dp(ctx, 42), 1.0f);
            dLp.leftMargin = dp(ctx, 6);
            doneBtn.setLayoutParams(dLp);
            doneBtn.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    Toast.makeText(ctx, "Tweaks saved", Toast.LENGTH_SHORT).show();
                    dialog.dismiss();
                }
            });

            btnRow.addView(resetBtn);
            btnRow.addView(doneBtn);
            panel.addView(btnRow);

            dialog.show();
            Window w = dialog.getWindow();
            if (w != null) {
                w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            }
        } catch (Throwable ignored) {
        }
    }

    private static void addTweakRow(
            final Context ctx,
            LinearLayout parent,
            String titleText,
            String descText,
            final String prefKey,
            boolean defaultVal
    ) {
        LinearLayout item = new LinearLayout(ctx);
        item.setOrientation(LinearLayout.HORIZONTAL);
        item.setGravity(Gravity.CENTER_VERTICAL);
        item.setPadding(dp(ctx, 12), dp(ctx, 11), dp(ctx, 12), dp(ctx, 11));

        LinearLayout.LayoutParams itemLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        itemLp.bottomMargin = dp(ctx, 8);
        item.setLayoutParams(itemLp);

        GradientDrawable itemBg = new GradientDrawable();
        itemBg.setCornerRadius(dp(ctx, 14));
        itemBg.setColor(0x55221A2A);
        itemBg.setStroke(dp(ctx, 1), 0x26FFFFFF);
        item.setBackground(itemBg);

        LinearLayout col = new LinearLayout(ctx);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f));

        TextView t = new TextView(ctx);
        t.setText(titleText);
        t.setTextColor(0xFFFFFFFF);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14.0f);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(t);

        TextView d = new TextView(ctx);
        d.setText(descText);
        d.setTextColor(0xA6FFFFFF);
        d.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11.5f);
        col.addView(d);

        Switch sw = new Switch(ctx);
        sw.setChecked(AdditionalToolsConfig.getBool(prefKey, defaultVal));
        sw.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                AdditionalToolsConfig.setBool(ctx, prefKey, isChecked);
            }
        });

        item.addView(col);
        item.addView(sw);
        parent.addView(item);
    }

    private static TextView buildDialogPill(Context ctx, String label, int bgColor) {
        TextView btn = new TextView(ctx);
        btn.setText(label);
        btn.setTextColor(0xFFFFFFFF);
        btn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14.0f);
        btn.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        btn.setGravity(Gravity.CENTER);
        btn.setClickable(true);
        btn.setFocusable(true);

        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(ctx, 21));
        bg.setColor(bgColor);
        bg.setStroke(dp(ctx, 1), 0x45FFFFFF);
        btn.setBackground(bg);
        return btn;
    }

    private static int dp(Context ctx, int value) {
        float density = ctx.getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }
}
