import java.io.*;
import java.net.*;

public class NtfyProbe {
    public static void main(String[] a) throws Exception {
        // 仅探针：绕过本机 JDK 信任库问题（手机端用系统证书，无此问题）
        javax.net.ssl.TrustManager[] tm = {new javax.net.ssl.X509TrustManager() {
            public void checkClientTrusted(java.security.cert.X509Certificate[] c, String t) {}
            public void checkServerTrusted(java.security.cert.X509Certificate[] c, String t) {}
            public java.security.cert.X509Certificate[] getAcceptedIssuers() {
                return new java.security.cert.X509Certificate[0];
            }
        }};
        javax.net.ssl.SSLContext sc = javax.net.ssl.SSLContext.getInstance("TLS");
        sc.init(null, tm, new java.security.SecureRandom());
        javax.net.ssl.HttpsURLConnection.setDefaultSSLSocketFactory(sc.getSocketFactory());

        long since = System.currentTimeMillis() / 1000 - 600;
        String url = "https://ntfy.sh/hketf-261d78de5272/json?since=" + since;
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setRequestProperty("Accept", "application/x-ndjson");
        conn.connect();
        System.out.println("HTTP " + conn.getResponseCode()
                + " | Content-Type=" + conn.getContentType()
                + " | Encoding=" + conn.getContentEncoding());
        final BufferedReader br = new BufferedReader(
                new InputStreamReader(conn.getInputStream(), "UTF-8"));
        // 3 秒后用 curl 发一条新消息
        new Thread(() -> {
            try {
                Thread.sleep(3000);
                Runtime.getRuntime().exec(new String[]{"bash", "-c",
                    "curl -s -X POST https://ntfy.sh/hketf-261d78de5272 "
                  + "-H 'Content-Type: application/json' "
                  + "-d '{\"title\":\"probe\",\"message\":\"probe msg\"}' >/dev/null"}).waitFor();
            } catch (Exception e) { e.printStackTrace(); }
        }).start();
        String line; int n = 0;
        while ((line = br.readLine()) != null) {
            System.out.println("LINE: " + line);
            if (++n > 6) break;
        }
    }
}
