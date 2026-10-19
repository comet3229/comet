# 매뉴얼 챗봇 설치 (한 번만 실행)
# 관리자 권한 없이 이 폴더 안에만 설치한다: tools(uv, 파이썬), .venv, engine(llama.cpp), models
param([switch]$Cpu)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = $PSScriptRoot
Set-Location $Root

$ChatRepos  = @('unsloth/Qwen3-4B-Instruct-2507-GGUF', 'Qwen/Qwen3-4B-GGUF')
$ChatFile   = '*Q4_K_M.gguf'
$EmbedRepos = @('gpustack/bge-m3-GGUF', 'lm-kit/bge-m3-gguf')
$EmbedFiles = @('*Q8_0.gguf', '*f16.gguf', '*F16.gguf')

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

function Download($url, $out) {
    $tmp = "$out.part"
    & curl.exe -fL --retry 3 -C - -o $tmp $url
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  curl 실패, PowerShell 로 다시 받습니다...'
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    }
    Move-Item -Force $tmp $out
}

function Get-GitHubAsset($repo, $pattern) {
    $rel = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$repo/releases/latest"
    $asset = $rel.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
    if (-not $asset) { throw "$repo 최신 릴리스에서 $pattern 파일을 찾지 못했어요." }
    return $asset
}

function Get-HFModel($repos, $patterns, $dest) {
    New-Item -ItemType Directory -Force $dest | Out-Null
    if (Get-ChildItem $dest -Filter *.gguf -ErrorAction SilentlyContinue) {
        Write-Host "  이미 있음: $dest"; return
    }
    foreach ($repo in $repos) {
        try { $info = Invoke-RestMethod -UseBasicParsing "https://huggingface.co/api/models/$repo" }
        catch { Write-Host "  $repo 를 열 수 없어요, 다음 후보로 넘어갑니다."; continue }
        foreach ($p in $patterns) {
            $file = $info.siblings.rfilename | Where-Object { $_ -like $p -and $_ -notlike '*/*' } | Select-Object -First 1
            if ($file) {
                Write-Host "  $repo / $file 받는 중 (용량이 커서 몇 분 걸려요)"
                Download "https://huggingface.co/$repo/resolve/main/$file" (Join-Path $dest $file)
                return
            }
        }
    }
    throw "모델을 받지 못했어요: $($repos -join ', ')"
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'tools\python'
$env:UV_CACHE_DIR = Join-Path $Root 'tools\uv-cache'

Step '1/4 uv (파이썬 설치 도구)'
$uv = Join-Path $Root 'tools\uv.exe'
if (-not (Test-Path $uv)) {
    New-Item -ItemType Directory -Force (Join-Path $Root 'tools') | Out-Null
    $a = Get-GitHubAsset 'astral-sh/uv' '^uv-x86_64-pc-windows-msvc\.zip$'
    $zip = Join-Path $Root 'tools\uv.zip'
    Download $a.browser_download_url $zip
    Expand-Archive -Force $zip (Join-Path $Root 'tools\uv-unzip')
    Copy-Item (Get-ChildItem (Join-Path $Root 'tools\uv-unzip') -Recurse -Filter uv.exe | Select-Object -First 1).FullName $uv
    Remove-Item -Recurse -Force $zip, (Join-Path $Root 'tools\uv-unzip')
}
Write-Host '  준비됨'

Step '2/4 파이썬과 라이브러리'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $uv venv .venv --python 3.12
    if ($LASTEXITCODE -ne 0) { throw '파이썬 환경을 만들지 못했어요.' }
}
& $uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '라이브러리를 설치하지 못했어요.' }

Step '3/4 AI 엔진 (llama.cpp)'
if (-not (Get-ChildItem engine -Recurse -Filter llama-server.exe -ErrorAction SilentlyContinue)) {
    $pattern = if ($Cpu) { 'bin-win-cpu-x64\.zip$' } else { 'bin-win-vulkan-x64\.zip$' }
    $a = Get-GitHubAsset 'ggml-org/llama.cpp' $pattern
    Write-Host "  $($a.name)"
    New-Item -ItemType Directory -Force engine | Out-Null
    Download $a.browser_download_url 'engine\llama.zip'
    Expand-Archive -Force 'engine\llama.zip' engine
    Remove-Item -Force 'engine\llama.zip'
}
Write-Host '  준비됨'

Step '4/4 AI 모델 (답변용 약 2.5GB, 검색용 약 0.6GB)'
Get-HFModel $ChatRepos @($ChatFile) 'models\chat'
Get-HFModel $EmbedRepos $EmbedFiles 'models\embed'

Write-Host "`n설치가 끝났어요. docs 폴더에 PDF 를 넣고 run.bat 을 실행하세요." -ForegroundColor Green
