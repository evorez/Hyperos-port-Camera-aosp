package androidx.preference;

import android.content.Context;

public abstract class PreferenceGroup extends Preference {
    public PreferenceGroup(Context context) { super(context); }
    public int getPreferenceCount() { return 0; }
    public Preference getPreference(int index) { return null; }
    public boolean addPreference(Preference preference) { return false; }
    public boolean removePreference(Preference preference) { return false; }
    public Preference findPreference(CharSequence key) { return null; }
}
