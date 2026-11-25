package androidx.preference;

import android.content.Context;

public class Preference {
    public Preference(Context context) {}
    public Context getContext() { return null; }
    public void setKey(String key) {}
    public String getKey() { return null; }
    public void setOrder(int order) {}
    public void setSelectable(boolean selectable) {}
    public void setPersistent(boolean persistent) {}
    public void setVisible(boolean visible) {}
    public CharSequence getTitle() { return null; }
    public CharSequence getSummary() { return null; }
    public void onBindViewHolder(PreferenceViewHolder holder) {}
}
