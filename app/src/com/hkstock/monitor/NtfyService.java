package com.hkstock.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 后台信号服务：长连接订阅专属频道，云端一有信号就发系统通知。
 * 不需要装 ntfy APP，也不需要微信；本服务只把频道当“消息中转”。
 * 断线自动退避重连，按最后一条消息 id 去重、补漏，重启不丢不重复。
 */
public class NtfyService extends Service {
    private static final String BASE = "https://ntfy.sh/hketf-261d78de5272";
    private static final String CH_FG = "hk_fg";    // 常驻低优先级渠道
    private static final String CH_SIG = "hk_sig";  // 交易信号高优先级渠道
    private static final String PREFS = "hk_notify";
    private static final String KEY_LAST = "last_id";
    private static final int FG_ID = 1;

    private volatile boolean running = false;
    private Thread worker;
    private int notifySeq = 100;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
        startForeground(FG_ID, buildFgNotification());
        running = true;
        worker = new Thread(new Runnable() {
            public void run() {
                loop();
            }
        }, "ntfy-sub");
        worker.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY; // 被系统回收后尽量自动重启
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    /** 持续订阅，断线退避重连。 */
    private void loop() {
        long backoff = 1000;
        while (running) {
            HttpURLConnection conn = null;
            try {
                String lastId = prefs().getString(KEY_LAST, "");
                String url;
                if (lastId.length() > 0) {
                    url = BASE + "/json?since=" + lastId;
                } else {
                    // 首次启动：拉最近 10 分钟的消息，避免漏掉服务启动前刚发的信号，也不翻旧账
                    long sinceSec = System.currentTimeMillis() / 1000L - 600L;
                    url = BASE + "/json?since=" + sinceSec;
                }
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(120000); // 靠服务端心跳保活，120秒没数据就重连
                conn.setUseCaches(false);
                conn.setRequestProperty("Accept", "application/x-ndjson");
                conn.setRequestProperty("Accept-Encoding", "identity");
                conn.connect();
                BufferedReader br = new BufferedReader(
                        new InputStreamReader(conn.getInputStream(), "UTF-8"));
                String line;
                boolean connected = false;
                while (running && (line = br.readLine()) != null) {
                    backoff = 1000; // 收到数据，重置退避
                    if (!connected) {
                        connected = true;
                        updateFg("看板运行中·已连接");
                    }
                    handleLine(line);
                }
            } catch (Exception e) {
                // 把连接失败原因显示到前台通知，便于真机定位
                updateFg("看板重连中：" + e.getClass().getSimpleName());
            } finally {
                if (conn != null) {
                    conn.disconnect();
                }
            }
            if (!running) {
                break;
            }
            try {
                Thread.sleep(backoff);
            } catch (InterruptedException ie) {
                break;
            }
            backoff = Math.min(backoff * 2, 15000);
        }
    }

    /** 解析一行事件，只处理新消息，去重后发通知。 */
    private void handleLine(String line) {
        try {
            JSONObject o = new JSONObject(line);
            if (!"message".equals(o.optString("event"))) {
                return;
            }
            String id = o.optString("id");
            String lastId = prefs().getString(KEY_LAST, "");
            if (id.length() > 0 && id.equals(lastId)) {
                return; // 重连补拉时跳过已处理的那条
            }
            String title = o.optString("title", "港股ETF看板");
            String message = o.optString("message", "");
            String click = o.optString("click", "");
            showSignal(title, message, click);
            if (id.length() > 0) {
                prefs().edit().putString(KEY_LAST, id).apply();
            }
        } catch (Exception ignored) {
        }
    }

    /** 发一条交易信号系统通知，点击复制代码并直接跳同花顺（透明，不露面）。 */
    private void showSignal(String title, String message, String click) {
        PendingIntent pi = jumpIntent(notifySeq, click);
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, CH_SIG);
        } else {
            b = new Notification.Builder(this);
            b.setPriority(Notification.PRIORITY_HIGH);
        }
        Notification n = b.setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        NotificationManager nm = (NotificationManager)
                getSystemService(Context.NOTIFICATION_SERVICE);
        nm.notify(notifySeq++, n);
    }

    /** 常驻前台通知（低优先级，可折叠）。 */
    private Notification buildFgNotification() {
        return buildFgNotification("正在后台盯信号");
    }

    private Notification buildFgNotification(String status) {
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, CH_FG);
        } else {
            b = new Notification.Builder(this);
            b.setPriority(Notification.PRIORITY_LOW);
        }
        return b.setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("看板运行中")
                .setContentText(status)
                .setOngoing(true)
                .setContentIntent(mainIntent(0))
                .build();
    }

    /** 刷新前台通知文字，反映连接状态/错误。 */
    private void updateFg(String status) {
        try {
            NotificationManager nm = (NotificationManager)
                    getSystemService(Context.NOTIFICATION_SERVICE);
            nm.notify(FG_ID, buildFgNotification(status));
        } catch (Exception ignored) {
        }
    }

    private PendingIntent mainIntent(int requestCode) {
        Intent it = new Intent(this, MainActivity.class);
        it.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int piFlag = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) {
            piFlag |= PendingIntent.FLAG_IMMUTABLE;
        }
        return PendingIntent.getActivity(this, requestCode, it, piFlag);
    }

    /** 信号通知点击：有 hketf 链接就走透明 JumpActivity（复制+跳同花顺），否则回看板。 */
    private PendingIntent jumpIntent(int requestCode, String click) {
        int piFlag = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) {
            piFlag |= PendingIntent.FLAG_IMMUTABLE;
        }
        if (click != null && click.length() > 0) {
            Intent it = new Intent(Intent.ACTION_VIEW, Uri.parse(click));
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            return PendingIntent.getActivity(this, requestCode, it, piFlag);
        }
        return mainIntent(requestCode);
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationManager nm = (NotificationManager)
                getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel fg = new NotificationChannel(
                CH_FG, "后台运行", NotificationManager.IMPORTANCE_LOW);
        fg.setShowBadge(false);
        NotificationChannel sig = new NotificationChannel(
                CH_SIG, "交易信号", NotificationManager.IMPORTANCE_HIGH);
        nm.createNotificationChannel(fg);
        nm.createNotificationChannel(sig);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
