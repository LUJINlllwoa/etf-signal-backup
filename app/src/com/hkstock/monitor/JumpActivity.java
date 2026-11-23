package com.hkstock.monitor;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.widget.Toast;

/**
 * 透明无界面跳转页：点信号通知后，静默复制代码、直接启动同花顺，然后立刻结束。
 * 用户看不到看板界面，也不经过浏览器，落地即同花顺。
 */
public class JumpActivity extends Activity {
    private static final String THS_PKG = "com.hexin.plat.android";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Uri data = getIntent() != null ? getIntent().getData() : null;
        String code = data != null ? data.getQueryParameter("code") : null;
        if (!TextUtils.isEmpty(code)) {
            ClipboardManager cm = (ClipboardManager)
                    getSystemService(Context.CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("code", code));
        }
        boolean launched = false;
        try {
            Intent ths = getPackageManager().getLaunchIntentForPackage(THS_PKG);
            if (ths != null) {
                ths.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                startActivity(ths);
                launched = true;
            }
        } catch (Exception ignored) {
        }
        if (TextUtils.isEmpty(code) || !launched) {
            String msg = TextUtils.isEmpty(code)
                    ? "未取到代码" : "代码已复制，没找到同花顺，请手动打开";
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show();
        }
        finish();
    }
}
