param(
    [string]$OutputDirectory = (
        Join-Path $PSScriptRoot "..\frontend\public\sounds"
    )
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Speech

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$workDirectory = Join-Path ([System.IO.Path]::GetTempPath()) (
    "mesil-notification-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $workDirectory | Out-Null

try {
    $speechPath = Join-Path $workDirectory "medic-speech.wav"
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synthesizer.SelectVoice("Microsoft Heami Desktop")
        $synthesizer.Rate = 1
        $synthesizer.Volume = 100
        $synthesizer.SetOutputToWaveFile($speechPath)
        $synthesizer.Speak("메딕")
    }
    finally {
        $synthesizer.Dispose()
    }

    $medicOutput = Join-Path $resolvedOutput "mesil-medic-v1.wav"
    & ffmpeg -hide_banner -loglevel error -y `
        -i $speechPath `
        -f lavfi -i "sine=frequency=659.25:duration=0.20:sample_rate=48000" `
        -f lavfi -i "sine=frequency=783.99:duration=0.24:sample_rate=48000" `
        -f lavfi -i "sine=frequency=987.77:duration=0.34:sample_rate=48000" `
        -filter_complex @"
[0:a]adelay=210|210,highpass=f=170,lowpass=f=6200,volume=1.18[speech];
[1:a]afade=t=in:st=0:d=0.018,afade=t=out:st=0.13:d=0.07,volume=0.30[tone1];
[2:a]adelay=150|150,afade=t=in:st=0:d=0.018,afade=t=out:st=0.16:d=0.08,volume=0.28[tone2];
[3:a]adelay=350|350,afade=t=in:st=0:d=0.02,afade=t=out:st=0.22:d=0.12,volume=0.24[tone3];
[speech][tone1][tone2][tone3]amix=inputs=4:duration=longest:normalize=0,
acompressor=threshold=0.22:ratio=3:attack=4:release=80,
loudnorm=I=-13:TP=-1.0:LRA=4,
apad=pad_dur=0.10,atrim=0:1.35,
aformat=sample_rates=48000:channel_layouts=stereo[out]
"@ `
        -map "[out]" -c:a pcm_s16le $medicOutput
    if ($LASTEXITCODE -ne 0) {
        throw "메딕 합성 알림음 생성에 실패했습니다."
    }

    $chimeOutput = Join-Path $resolvedOutput "mesil-chime-v1.wav"
    & ffmpeg -hide_banner -loglevel error -y `
        -f lavfi -i "sine=frequency=587.33:duration=0.20:sample_rate=48000" `
        -f lavfi -i "sine=frequency=739.99:duration=0.24:sample_rate=48000" `
        -f lavfi -i "sine=frequency=880.00:duration=0.38:sample_rate=48000" `
        -filter_complex @"
[0:a]afade=t=in:st=0:d=0.018,afade=t=out:st=0.13:d=0.07,volume=0.46[tone1];
[1:a]adelay=180|180,afade=t=in:st=0:d=0.018,afade=t=out:st=0.16:d=0.08,volume=0.42[tone2];
[2:a]adelay=390|390,afade=t=in:st=0:d=0.02,afade=t=out:st=0.24:d=0.14,volume=0.38[tone3];
[tone1][tone2][tone3]amix=inputs=3:duration=longest:normalize=0,
acompressor=threshold=0.24:ratio=2.5:attack=4:release=90,
loudnorm=I=-12.5:TP=-1.0:LRA=4,
apad=pad_dur=0.12,atrim=0:1.10,
aformat=sample_rates=48000:channel_layouts=stereo[out]
"@ `
        -map "[out]" -c:a pcm_s16le $chimeOutput
    if ($LASTEXITCODE -ne 0) {
        throw "차임 알림음 생성에 실패했습니다."
    }

    Get-Item -LiteralPath $medicOutput, $chimeOutput |
        Select-Object FullName, Length
}
finally {
    if (Test-Path -LiteralPath $workDirectory) {
        Remove-Item -LiteralPath $workDirectory -Recurse -Force
    }
}
