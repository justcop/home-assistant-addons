package uk.co.justcop.audioshelf.helper;

import java.util.ArrayDeque;

/** Bounded log retaining startup context and the latest outcome, in event order. */
final class WakeLog {
    private static final int STARTUP_LIMIT = 6000;
    private static final int RECENT_LIMIT = 10000;
    private static final int LINE_LIMIT = 2000;
    private final StringBuilder startup = new StringBuilder();
    private final ArrayDeque<String> recent = new ArrayDeque<>();
    private boolean startupComplete;
    private int recentLength, omitted;

    void clear() {
        startup.setLength(0);
        recent.clear();
        startupComplete = false;
        recentLength = 0;
        omitted = 0;
    }

    void append(String line) {
        if (line.length() > LINE_LIMIT) line = line.substring(0, LINE_LIMIT) + "…";
        line += "\n";
        if (!startupComplete && startup.length() + line.length() <= STARTUP_LIMIT) {
            startup.append(line);
            return;
        }
        startupComplete = true;
        recent.addLast(line);
        recentLength += line.length();
        while (recentLength > RECENT_LIMIT) {
            recentLength -= recent.removeFirst().length();
            omitted++;
        }
    }

    @Override public String toString() {
        StringBuilder text = new StringBuilder(startup);
        if (omitted > 0) text.append("[... ").append(omitted)
            .append(" middle log entries omitted; startup and latest events retained ...]\n");
        for (String line : recent) text.append(line);
        return text.toString();
    }
}
