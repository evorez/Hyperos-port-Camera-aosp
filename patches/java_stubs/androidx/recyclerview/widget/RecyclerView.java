package androidx.recyclerview.widget;

import android.view.View;

public class RecyclerView {
    public static abstract class ViewHolder {
        public final View itemView;
        public ViewHolder(View itemView) {
            this.itemView = itemView;
        }
    }
}
