package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.function.Consumer;

/**
 * The helper is a transient handoff, not a control panel.
 * Keep the branded connection surface separate from diagnostics and maintenance.
 */
final class HelperScreen {
    private static final int INK = Color.rgb(37, 39, 37);
    private static final int WINE = Color.rgb(116, 47, 56);
    private static final int MUTED = Color.rgb(104, 106, 104);

    final TextView message;
    final TextView wakeLog;
    final TextView pairingLog;
    final TextView updateStatus;
    final Button pairButton;
    private final ProgressBar spinner;
    private final View home;
    private final View diagnosticPanel;
    private boolean diagnosticsOpen;

    HelperScreen(Activity activity, boolean waking, Runnable pairAction, Runnable updateAction,
                 Runnable returnAction, Consumer<Boolean> holdChange) {
        LinearLayout root = new LinearLayout(activity);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(250, 248, 245));
        root.setPadding(dp(activity, 20), dp(activity, 10), dp(activity, 20), dp(activity, 16));

        LinearLayout header = new LinearLayout(activity);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView wordmark = label(activity, "AUDIOSHELF", 12, WINE);
        wordmark.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        wordmark.setLetterSpacing(0.16f);
        wordmark.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(wordmark, new LinearLayout.LayoutParams(0, dp(activity, 48), 1));

        TextView overflow = label(activity, "⋮", 28, INK);
        overflow.setGravity(Gravity.CENTER);
        overflow.setContentDescription("More options: diagnostics and maintenance");
        overflow.setOnClickListener(v -> {
            PopupMenu menu = new PopupMenu(activity, overflow);
            menu.getMenu().add(diagnosticsOpen ? "Back to helper" : "Diagnostics & maintenance");
            menu.setOnMenuItemClickListener(item -> {
                if (diagnosticsOpen) showHome();
                else showDiagnostics();
                return true;
            });
            menu.show();
        });
        header.addView(overflow, new LinearLayout.LayoutParams(dp(activity, 48), dp(activity, 48)));
        root.addView(header);

        FrameLayout stage = new FrameLayout(activity);
        root.addView(stage, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        LinearLayout branding = new LinearLayout(activity);
        branding.setGravity(Gravity.CENTER);
        branding.setOrientation(LinearLayout.VERTICAL);
        branding.setPadding(dp(activity, 12), 0, dp(activity, 12), dp(activity, 36));

        ImageView logo = new ImageView(activity);
        logo.setImageResource(R.drawable.ic_helper);
        logo.setContentDescription("AudioShelf logo. Long press for diagnostics.");
        logo.setOnLongClickListener(v -> { showDiagnostics(); return true; });
        branding.addView(logo, new LinearLayout.LayoutParams(dp(activity, 156), dp(activity, 156)));

        TextView title = label(activity, "AudioShelf", 30, INK);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-2, -2);
        titleParams.topMargin = dp(activity, 18);
        branding.addView(title, titleParams);

        TextView subtitle = label(activity, "SPOTIFY COMPANION", 11, WINE);
        subtitle.setLetterSpacing(0.18f);
        subtitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(-2, -2);
        subtitleParams.topMargin = dp(activity, 8);
        branding.addView(subtitle, subtitleParams);

        spinner = new ProgressBar(activity);
        spinner.setIndeterminateTintList(ColorStateList.valueOf(WINE));
        LinearLayout.LayoutParams spinnerParams =
            new LinearLayout.LayoutParams(dp(activity, 28), dp(activity, 28));
        spinnerParams.topMargin = dp(activity, 36);
        branding.addView(spinner, spinnerParams);
        spinner.setVisibility(waking ? View.VISIBLE : View.GONE);

        message = label(activity, waking ? "Connecting to Spotify…" : "Ready for AudioShelf", 15, MUTED);
        message.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams messageParams = new LinearLayout.LayoutParams(-1, -2);
        messageParams.topMargin = dp(activity, 16);
        branding.addView(message, messageParams);

        pairButton = action(activity, "Connect Spotify", branding, pairAction);
        pairButton.setVisibility(waking ? View.GONE : View.VISIBLE);
        stage.addView(branding, new FrameLayout.LayoutParams(-1, -1));
        home = branding;

        ScrollView advanced = new ScrollView(activity);
        advanced.setVisibility(View.GONE);
        LinearLayout options = new LinearLayout(activity);
        options.setOrientation(LinearLayout.VERTICAL);
        options.setPadding(dp(activity, 4), dp(activity, 12), dp(activity, 4), dp(activity, 28));
        advanced.addView(options);
        stage.addView(advanced, new FrameLayout.LayoutParams(-1, -1));
        diagnosticPanel = advanced;

        TextView heading = label(activity, "Diagnostics & maintenance", 22, INK);
        heading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        options.addView(heading);
        options.addView(label(activity, "AudioShelf helper " + BuildConfig.VERSION_NAME, 13, MUTED));

        updateStatus = label(activity, "", 13, MUTED);
        action(activity, "Check for updates", options, updateAction);
        options.addView(updateStatus);

        CheckBox hold = new CheckBox(activity);
        hold.setText("Keep this wake open for diagnostics");
        hold.setTextColor(INK);
        hold.setVisibility(waking ? View.VISIBLE : View.GONE);
        hold.setOnCheckedChangeListener((button, checked) -> holdChange.accept(checked));
        options.addView(hold);

        TextView wakeHeading = label(activity, "Wake log", 17, INK);
        wakeHeading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        options.addView(wakeHeading);
        wakeLog = label(activity, "No wake attempt recorded yet.", 12, INK);
        wakeLog.setTypeface(Typeface.MONOSPACE);
        wakeLog.setTextIsSelectable(true);
        action(activity, "Copy wake log", options,
            () -> HelperScreen.copy(activity, "AudioShelf wake log", wakeLog.getText()));
        options.addView(wakeLog);

        TextView pairingHeading = label(activity, "Pairing log", 17, INK);
        pairingHeading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams pairHeadingParams = new LinearLayout.LayoutParams(-1, -2);
        pairHeadingParams.topMargin = dp(activity, 18);
        options.addView(pairingHeading, pairHeadingParams);
        pairingLog = label(activity, "No pairing attempt recorded yet.", 12, INK);
        pairingLog.setTypeface(Typeface.MONOSPACE);
        pairingLog.setTextIsSelectable(true);
        action(activity, "Copy pairing log", options,
            () -> HelperScreen.copy(activity, "AudioShelf pairing log", pairingLog.getText()));
        options.addView(pairingLog);

        action(activity, waking ? "Return to AudioShelf" : "Back to helper", options, returnAction);
        activity.setContentView(root);
    }

    void setLoading(boolean loading) {
        spinner.setVisibility(loading ? View.VISIBLE : View.GONE);
    }

    void setSetupState(boolean trusted, boolean authorised, boolean pairing) {
        pairButton.setEnabled(trusted && !pairing);
        pairButton.setText(authorised ? "Reconnect Spotify" : "Connect Spotify");
        if (!pairing) {
            message.setText(!trusted ? "Open AudioShelf and press Play once to complete setup."
                : authorised ? "Spotify connected. Ready for AudioShelf."
                : "Connect Spotify once to enable background playback.");
        }
    }

    void showDiagnostics() {
        diagnosticsOpen = true;
        home.setVisibility(View.GONE);
        diagnosticPanel.setVisibility(View.VISIBLE);
    }

    void showHome() {
        diagnosticsOpen = false;
        diagnosticPanel.setVisibility(View.GONE);
        home.setVisibility(View.VISIBLE);
    }

    private static TextView label(Activity activity, String text, int fontSize, int colour) {
        TextView view = new TextView(activity);
        view.setText(text);
        view.setTextSize(fontSize);
        view.setTextColor(colour);
        view.setPadding(0, dp(activity, 5), 0, dp(activity, 5));
        return view;
    }

    private static Button action(Activity activity, String caption, LinearLayout parent,
                                 Runnable callback) {
        Button button = new Button(activity);
        button.setAllCaps(false);
        button.setText(caption);
        button.setOnClickListener(v -> callback.run());
        parent.addView(button, new LinearLayout.LayoutParams(-1, -2));
        return button;
    }

    private static int dp(Activity activity, int value) {
        return Math.round(value * activity.getResources().getDisplayMetrics().density);
    }

    private static void copy(Activity activity, String name, CharSequence value) {
        android.content.ClipboardManager clipboard =
            (android.content.ClipboardManager) activity.getSystemService(Activity.CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText(name, value));
        android.widget.Toast.makeText(activity, "Log copied", android.widget.Toast.LENGTH_SHORT).show();
    }
}
