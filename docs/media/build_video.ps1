# Build the public guide video from the fictional slides in this directory.
# Audio is generated locally with an installed Windows voice by default. Pass
# -AudioDirectory with narration-00.wav through narration-05.wav to use audio
# exported from Voicebox instead.
param([string]$Voice = 'Microsoft Mark', [string]$AudioDirectory = '')

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$scratch = Join-Path $repo '.tools\video-build'
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

$lines = @(
  'WARDOGS Presence turns your War Dogs game status into one tidy Discord message. It runs from a single portable Windows executable.',
  'Download the Windows EXE from the project releases and open it. The interface, screen reader, and OCR data are already inside. Your settings stay on this computer.',
  'In Discord, create a webhook for each channel you want to update. Paste each URL into Discord channels in the app, test the connection, and enable the destinations you want.',
  'With the game pause menu visible, capture a frame. Drag the server, faction, and score zones over the matching parts of the screen. Run the OCR check and adjust until the readback looks right.',
  'Start the watcher when you are ready to play. The app reads the game screen locally, then edits one existing message in each enabled channel. Each webhook has its own cooldown and respects Discord retry delays.',
  'Your friends see a match, queue, or out-of-game status in one place. The game screenshot stays local; only status text goes to the Discord webhooks you enabled. The full setup guide is in the app and on GitHub Pages.'
)

$speaker = $null
if (-not $AudioDirectory) {
  Add-Type -AssemblyName System.Speech
  $speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
}
try {
  if ($speaker) {
    $speaker.SelectVoice($Voice)
    $speaker.Rate = 1
  }
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $number = '{0:D2}' -f $i
    $wav = Join-Path $scratch "narration-$number.wav"
    if ($AudioDirectory) {
      $source = Join-Path $AudioDirectory "narration-$number.wav"
      if (-not (Test-Path -LiteralPath $source)) { throw "Missing $source" }
      Copy-Item -LiteralPath $source -Destination $wav -Force
    } else {
      $speaker.SetOutputToWaveFile($wav)
      $speaker.Speak($lines[$i])
      $speaker.SetOutputToNull()
    }
    $slide = Join-Path $PSScriptRoot "slide-$number.png"
    $clip = Join-Path $scratch "clip-$number.mp4"
    & ffmpeg -hide_banner -loglevel error -y -loop 1 -framerate 30 -i $slide -i $wav -af 'apad=pad_dur=0.7' -c:v libx264 -preset veryfast -crf 23 -tune stillimage -c:a aac -b:a 128k -pix_fmt yuv420p -shortest $clip
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed on slide $number" }
  }
  $concat = Join-Path $scratch 'concat.txt'
  $items = for ($i = 0; $i -lt $lines.Count; $i++) {
    $number = '{0:D2}' -f $i
    "file 'clip-$number.mp4'"
  }
  [IO.File]::WriteAllLines($concat, $items, [Text.Encoding]::ASCII)
  & ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i $concat -c copy -movflags +faststart (Join-Path $PSScriptRoot 'setup-guide.mp4')
  if ($LASTEXITCODE -ne 0) { throw 'ffmpeg concat failed' }
  Write-Output "Built $PSScriptRoot\setup-guide.mp4"
}
finally {
  if ($speaker) { $speaker.Dispose() }
  $resolvedRepo = [IO.Path]::GetFullPath($repo).TrimEnd('\')
  $resolvedScratch = [IO.Path]::GetFullPath($scratch)
  if ($resolvedScratch.StartsWith($resolvedRepo + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force -ErrorAction SilentlyContinue
  }
}
