package com.hkstock.monitor;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.JavascriptInterface;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.content.ContentValues;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class MainActivity extends Activity {
    private static final String THS_PKG = "com.hexin.plat.android";
    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        FrameLayout root = new FrameLayout(this);

        webView = new WebView(this);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        webView.setWebViewClient(new WebViewClient());
        // 网页把要保存的 JSON 文本通过这个接口交给原生，写入手机下载目录
        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void saveFile(final String filename, final String content) {
                try {
                    final String path = writeDownloads(filename, content);
                    runOnUiThread(new Runnable() {
                        public void run() {
                            Toast.makeText(MainActivity.this,
                                    "已保存到：下载/港股ETF看板/" + filename,
                                    Toast.LENGTH_LONG).show();
                        }
                    });
                } catch (final Exception e) {
                    runOnUiThread(new Runnable() {
                        public void run() {
                            Toast.makeText(MainActivity.this, "保存失败：" + e.getMessage(),
                                    Toast.LENGTH_LONG).show();
                        }
                    });
                }
            }
        }, "AndroidFile");
        webView.loadUrl("file:///android_asset/index.html");
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
        startSignalService();
        handleDeepLink(getIntent());
    }

    /** 启动后台信号服务（前台长连接），并申请通知权限。 */
    private void startSignalService() {
        // POST_NOTIFICATIONS 是 Android 13 权限，用字符串以兼容低版本编译期
        final String permPost = "android.permission.POST_NOTIFICATIONS";
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(permPost)
                        != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{permPost}, 10);
        }
        Intent svc = new Intent(this, NtfyService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleDeepLink(intent);
    }

    /** 把文本内容写成 JSON 文件。Android 10+ 用 MediaStore 存到公共下载目录（无需权限）；旧系统存到应用专属下载目录。 */
    private String writeDownloads(String filename, String content) throws Exception {
        byte[] bytes = content.getBytes("UTF-8");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ContentValues cv = new ContentValues();
            cv.put(MediaStore.Downloads.DISPLAY_NAME, filename);
            cv.put(MediaStore.Downloads.MIME_TYPE, "application/json");
            cv.put(MediaStore.Downloads.RELATIVE_PATH,
                    Environment.DIRECTORY_DOWNLOADS + "/港股ETF看板");
            Uri uri = getContentResolver().insert(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
            if (uri == null) {
                throw new Exception("无法创建文件");
            }
            OutputStream os = getContentResolver().openOutputStream(uri);
            os.write(bytes);
            os.close();
            return Environment.DIRECTORY_DOWNLOADS + "/港股ETF看板/" + filename;
        } else {
            File dir = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS),
                    "港股ETF看板");
            if (!dir.exists()) {
                dir.mkdirs();
            }
            File f = new File(dir, filename);
            FileOutputStream fos = new FileOutputStream(f);
            fos.write(bytes);
            fos.close();
            return f.getAbsolutePath();
        }
    }

    /** 点通知拉起本页：自动复制代码，然后立刻跳回同花顺。 */
    private void handleDeepLink(Intent intent) {
        Uri data = intent != null ? intent.getData() : null;
        if (data == null) {
            return;
        }
        String scheme = data.getScheme();
        boolean linkOk = "hketf".equals(scheme)
                || ("https".equals(scheme)
                        && "signal.hkboard.invalid".equals(data.getHost()));
        if (!linkOk) {
            return;
        }
        String code = data.getQueryParameter("code");
        if (TextUtils.isEmpty(code)) {
            return;
        }
        ClipboardManager cm = (ClipboardManager)
                getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("code", code));
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
        if (launched) {
            Toast.makeText(this, "代码 " + code + " 已复制，正在回同花顺",
                    Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "代码 " + code + " 已复制；没找到同花顺，请手动打开",
                    Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
