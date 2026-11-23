#!/bin/bash
set -e
export JAVA_HOME=/home/user/.doubao/agent_mode/workspace/jdk-17.0.21+9
export PATH=$JAVA_HOME/bin:$PATH
BT=/home/user/.doubao/agent_mode/workspace/android-sdk/build-tools/33.0.0
SDK=/home/user/.doubao/agent_mode/workspace/android-sdk
BUILD=/home/user/.doubao/agent_mode/workspace/apk_build
cd $BUILD

echo "== 1. aapt 打包 manifest/assets =="
$BT/aapt package -f -M AndroidManifest.xml -A assets -I $SDK/platforms/android-12/android.jar -F app-base.apk

echo "== 2. javac 编译 MainActivity =="
rm -rf obj && mkdir obj
javac -source 8 -target 8 -nowarn -bootclasspath $SDK/platforms/android-12/android.jar -d obj $(find src -name "*.java")

echo "== 3. d8 生成 classes.dex =="
rm -rf dex_out && mkdir dex_out
$BT/d8 --output dex_out $(find obj -name "*.class") --lib $SDK/platforms/android-12/android.jar

echo "== 4. 把 dex 加入 APK =="
cp app-base.apk app-unsigned.apk
(cd dex_out && $BT/aapt add ../app-unsigned.apk classes.dex)

echo "== 5. zipalign =="
$BT/zipalign -f 4 app-unsigned.apk app-aligned.apk

echo "== 6. apksigner 用 debug.keystore 签名 =="
$BT/apksigner sign --ks /home/user/.android/debug.keystore \
  --ks-pass pass:android --key-pass pass:android --ks-key-alias androiddebugkey \
  --out 港股ETF看板.apk app-aligned.apk

echo "== 7. 验证签名 =="
$BT/apksigner verify --verbose 港股ETF看板.apk
echo "BUILD_OK"
