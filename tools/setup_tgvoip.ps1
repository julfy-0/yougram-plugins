# Скачивает пустые git-submodule'ы Telegram (third_party/*, td) в app/src/main/cpp/tgvoip на тех же коммитах, что у Telegram-Android.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File tools\setup_tgvoip.ps1
$ErrorActionPreference = "Continue"
$tg = Join-Path (Split-Path -Parent $PSScriptRoot) "app\src\main\cpp\tgvoip"

$items = @(
    @{ Path = "third_party/absl";         Marker = "CMakeLists.txt";            Sha = "54fac219c4ef0bc379dfffb0b8098725d77ac81b"; Urls = @("https://github.com/abseil/abseil-cpp.git") },
    @{ Path = "third_party/boringssl";    Marker = "include/openssl/ssl.h";     Sha = "2b44a3701a4788e1ef866ddc7f143060a3d196c9"; Urls = @("https://github.com/google/boringssl.git") },
    @{ Path = "third_party/dav1d";        Marker = "meson.build";               Sha = "54706fc6bc0cdecab7e9593974a4039cc038fca7"; Urls = @("https://github.com/videolan/dav1d.git") },
    @{ Path = "third_party/ffmpeg";       Marker = "libavcodec/avcodec.h";      Sha = "45f1910444f34b02621f9f0426ea1a538a613c41"; Urls = @("https://github.com/FFmpeg/FFmpeg.git") },
    @{ Path = "third_party/libvpx";       Marker = "vpx/vpx_codec.h";           Sha = "1024874c5919305883187e2953de8fcb4c3d7fa6"; Urls = @("https://github.com/webmproject/libvpx.git") },
    @{ Path = "third_party/libyuv";       Marker = "CMakeLists.txt";            Sha = "28ce69c2744a6aafdb58564e7b884aec3f66be5f"; Urls = @("https://chromium.googlesource.com/libyuv/libyuv", "https://github.com/lemenkov/libyuv.git") },
    @{ Path = "third_party/openh264";     Marker = "codec/api/wels/codec_api.h"; Sha = "652bdb7719f30b52b08e506645a7322ff1b2cc6f"; Urls = @("https://github.com/cisco/openh264.git") },
    @{ Path = "third_party/wamr";         Marker = "core/iwasm/include/wasm_export.h"; Sha = "25bd7eb63e828e4bd242cc9b38d260b4b31c6605"; Urls = @("https://github.com/wasm-micro-runtime/wasm-micro-runtime.git") },
    @{ Path = "third_party/xiph/ogg";     Marker = "include/ogg/ogg.h";         Sha = "be05b13e98b048f0b5a0f5fa8ce514d56db5f822"; Urls = @("https://github.com/xiph/ogg.git") },
    @{ Path = "third_party/xiph/opus";    Marker = "include/opus.h";            Sha = "22244de5a79bd1d6d623c32e72bf1954b56235be"; Urls = @("https://github.com/xiph/opus.git") },
    @{ Path = "third_party/xiph/opusfile"; Marker = "include/opusfile.h";       Sha = "a55c164e9891a9326188b7d4d216ec9a88373739"; Urls = @("https://github.com/xiph/opusfile.git") },
    @{ Path = "td";                       Marker = "CMakeLists.txt";            Sha = "022d60202e446ad1287b9fb68e687c8a0760788b"; Urls = @("https://github.com/tdlib/td.git") }
)

foreach ($it in $items) {
    $dest = Join-Path $tg $it.Path
    if (Test-Path (Join-Path $dest $it.Marker)) { Write-Host "skip  $($it.Path)"; continue }
    $tmp = Join-Path $env:TEMP ("tgv_" + [guid]::NewGuid().ToString("N"))
    $ok = $false
    foreach ($url in $it.Urls) {
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        New-Item -ItemType Directory -Force $tmp | Out-Null
        git -C $tmp init -q
        git -C $tmp remote add origin $url
        git -C $tmp fetch -q --depth 1 origin $it.Sha 2>$null
        if ($LASTEXITCODE -eq 0) {
            git -C $tmp checkout -q FETCH_HEAD 2>$null
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        }
    }
    if (-not $ok) { Write-Host "FAIL  $($it.Path)"; continue }
    Remove-Item -Recurse -Force (Join-Path $tmp ".git")
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $tmp "*") $dest -Recurse -Force
    Remove-Item -Recurse -Force $tmp
    Write-Host "ok    $($it.Path)"
}
