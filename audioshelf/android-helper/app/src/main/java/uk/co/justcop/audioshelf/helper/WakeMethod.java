package uk.co.justcop.audioshelf.helper;

/** Compare initial connection timing without changing the SDK call or playback policy. */
enum WakeMethod {
    CURRENT("current", "Current wake method", "after the helper becomes visible (0.1.17 timing)"),
    PREVIOUS("previous", "Previous wake method", "immediately after request approval (0.1.14 timing)");

    final String preference, label, timing;

    WakeMethod(String preference, String label, String timing) {
        this.preference = preference;
        this.label = label;
        this.timing = timing;
    }

    static WakeMethod fromPreference(String value) {
        return PREVIOUS.preference.equals(value) ? PREVIOUS : CURRENT;
    }

    boolean shouldStart(boolean resumed, boolean wakeRequest, boolean started, boolean ready) {
        return wakeRequest && ready && !started && (this == PREVIOUS || resumed);
    }
}
